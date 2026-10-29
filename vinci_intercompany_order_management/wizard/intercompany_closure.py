"""Package 3D: finalize an intercompany cancellation after physical and
financial reversals have been *completed*, never simply prepared.

This is intentionally conservative: unexpected structure requires a manual
review. No completed stock move or posted accounting entry is deleted.
"""
import logging

from psycopg2 import errors

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class VinciIntercompanyClosure(models.TransientModel):
    _inherit = "vinci.intercompany.order.review.wizard"

    def _vinci_closure_invoice_sources(self, summary):
        central = self.sale_order_id
        distributor = summary["counterpart_orders"]
        purchase = summary["purchase_orders"]
        groups = (
            (central.invoice_ids, central.company_id, "out_invoice", central.order_line),
            (distributor.invoice_ids, distributor.company_id, "out_invoice", distributor.order_line),
            (purchase.invoice_ids, purchase.company_id, "in_invoice", purchase.order_line),
        )
        sources = self.env["account.move"]
        for invoices, company, expected_type, order_lines in groups:
            for invoice in invoices:
                if invoice.company_id != company:
                    raise UserError(_("Invoice company mismatch: %s") % invoice.display_name)
                if invoice.move_type == ("out_refund" if expected_type == "out_invoice" else "in_refund"):
                    # A linked refund must still be verified by its reversal link.
                    continue
                if invoice.move_type != expected_type or invoice.state != "posted":
                    raise UserError(_("Invoice %s requires manual review before closure.") % invoice.display_name)
                product_lines = invoice.invoice_line_ids.filtered(lambda l: l.display_type == "product")
                if not product_lines:
                    raise UserError(_("Invoice %s has no product lines.") % invoice.display_name)
                for line in product_lines:
                    related = (line.sale_line_ids if expected_type == "out_invoice"
                               else line.purchase_line_id)
                    if not related or (related - order_lines):
                        raise UserError(_("Invoice %s contains unrelated lines; manual review required.") % invoice.display_name)
                sources |= invoice
        if len(sources) != sum(
            len(invoices.filtered(lambda i: i.move_type == kind))
            for invoices, _company, kind, _lines in groups
        ):
            raise UserError(_("An invoice is shared across intercompany documents."))
        return sources

    def _vinci_closure_accounting_status(self, summary):
        """Return 'none', 'needs_reversal', 'draft_reversal', or 'ready'."""
        sources = self._vinci_closure_invoice_sources(summary)
        if not sources:
            if summary["sale_invoices"] or summary["purchase_invoices"]:
                raise UserError(_("Linked accounting entries exist without a supported source invoice."))
            return "none", sources, self.env["account.move"]
        reversals = self.env["account.move"]
        missing = False
        pending = False
        for invoice in sources:
            matches = self.env["account.move"].search([
                ("reversed_entry_id", "=", invoice.id),
            ])
            if not matches:
                missing = True
                continue
            if len(matches) != 1:
                raise UserError(_("Invoice %s has multiple reversals. Manual review required.") % invoice.name)
            reversal = matches[0]
            expected = "out_refund" if invoice.move_type == "out_invoice" else "in_refund"
            if reversal.company_id != invoice.company_id or reversal.move_type != expected:
                raise UserError(_("Invalid credit note/vendor refund for %s.") % invoice.name)
            if reversal.state == "cancel":
                raise UserError(_("A reversed document was cancelled for %s. Manual review required.") % invoice.name)
            if reversal.state != "posted":
                pending = True
            if invoice.currency_id != reversal.currency_id or invoice.currency_id.compare_amounts(
                invoice.amount_total, reversal.amount_total
            ) != 0:
                raise UserError(_("Reversal amount/currency differs from invoice %s.") % invoice.name)
            reversals |= reversal
        if missing and reversals:
            # Do not create additional credits automatically after partial preparation.
            raise UserError(_("Only some invoices have reversals. Complete remaining credits manually."))
        if pending:
            return "draft_reversal", sources, reversals
        if missing:
            return "needs_reversal", sources, reversals
        return "ready", sources, reversals

    def _vinci_closure_stock_status(self, summary):
        """Return whether all completed original moves have fully completed returns."""
        original_moves = (
            self.sale_order_id.order_line.move_ids
            | summary["counterpart_orders"].order_line.move_ids
            | summary["purchase_orders"].order_line.move_ids
        ).filtered(lambda m: not m.origin_returned_move_id)
        if not original_moves:
            raise UserError(_("Cannot find original stock moves for closure."))
        original_pickings = original_moves.mapped("picking_id")
        if not original_pickings or any(not m.picking_id for m in original_moves):
            raise UserError(_("Original stock moves are missing their transfers."))
        if any(p.company_id not in (self.sale_order_id.company_id | summary["purchase_orders"].company_id)
               for p in original_pickings):
            raise UserError(_("An original transfer belongs to an unexpected company."))
        open_moves = original_moves.filtered(lambda m: m.state not in ("done", "cancel"))
        if any(m.picked or any(m.move_line_ids.mapped("picked")) for m in open_moves):
            raise UserError(_("Picked open stock operations require manual review."))
        completed = original_moves.filtered(lambda m: m.state == "done")
        returns = self.env["stock.move"]
        missing = False
        for move in completed:
            matches = self.env["stock.move"].search([
                ("origin_returned_move_id", "=", move.id),
            ])
            if not matches:
                missing = True
                continue
            if any(m.state != "done" for m in matches):
                missing = True
                continue
            if any(r.company_id != move.company_id or r.product_id != move.product_id
                   for r in matches):
                raise UserError(_("Incorrect stock return for original move %s.") % move.id)
            total_returned = sum(
                r.product_uom._compute_quantity(r.quantity, move.product_uom)
                for r in matches
            )
            if float_compare(total_returned, move.quantity,
                             precision_rounding=move.product_uom.rounding) != 0:
                raise UserError(_("Return quantity mismatch for move %s.") % move.id)
            returns |= matches
        return {
            "ready": not missing,
            "completed": completed,
            "open_moves": open_moves,
            "original_moves": original_moves,
            "returns": returns,
            "original_pickings": original_pickings,
        }

    def _vinci_close_returned_intercompany(self):
        """Cancel outstanding demand on all three orders; retain done history."""
        self.ensure_one()
        if self.operation != "cancellation" or not self.env.user.has_group(
            "sales_team.group_sale_manager"
        ):
            raise UserError(_("Only Sales Managers can finalize intercompany cancellations."))
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        central, distributor, po = (
            self.sale_order_id, summary["counterpart_orders"], summary["purchase_orders"]
        )
        if len(distributor) != 1 or len(po) != 1 or (central.state != "sale"
                or distributor.state != "sale" or po.state != "purchase"):
            raise UserError(_("The three linked orders must still be confirmed."))
        before_stock = self._vinci_closure_stock_status(summary)
        accounting_state, sources, reversals = self._vinci_closure_accounting_status(summary)
        if not before_stock["ready"]:
            raise UserError(_("Some physical returns are not completed. Validate returns first."))
        if accounting_state not in ("none", "ready"):
            raise UserError(_("Credit notes/vendor refunds must be posted before final closure."))
        if sources and not self.env.user.has_group("account.group_account_invoice"):
            raise UserError(_("Invoice access is required to finalize invoiced orders."))

        signature = (
            tuple(sorted(before_stock["original_moves"].ids)),
            tuple(sorted(before_stock["returns"].ids)),
            tuple(sorted(sources.ids)), tuple(sorted(reversals.ids)),
        )
        self._vinci_lock_revision_documents(summary)
        if sources or reversals:
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute(
                        "SELECT id FROM account_move WHERE id = ANY(%s) ORDER BY id FOR UPDATE NOWAIT",
                        (sorted((sources | reversals).ids),),
                    )
            except errors.LockNotAvailable:
                raise UserError(_("Accounting documents are locked. Try again later."))
        self.env.invalidate_all()
        summary = central._vinci_get_intercompany_document_summary()
        now_stock = self._vinci_closure_stock_status(summary)
        now_accounting, sources_now, reversals_now = self._vinci_closure_accounting_status(summary)
        if signature != (tuple(sorted(now_stock["original_moves"].ids)),
                         tuple(sorted(now_stock["returns"].ids)),
                         tuple(sorted(sources_now.ids)), tuple(sorted(reversals_now.ids))):
            raise UserError(_("Documents changed during closure. Reopen the wizard."))
        if not now_stock["ready"] or now_accounting not in ("none", "ready"):
            raise UserError(_("A required stock or accounting reversal is not complete."))
        done_ids = set(now_stock["completed"].ids)
        old_move_ids = set(now_stock["original_moves"].ids)
        _logger.info(
            "VINCI_INTERCOMPANY | FINAL_CLOSURE_START | SO=%s | USER=%s",
            central.name, self.env.user.id,
        )
        # Odoo standard business methods: never force states with SQL/write().
        # If an override opens a confirmation wizard, our verification rejects
        # the incomplete operation and the current transaction rolls back.
        central.with_company(central.company_id).action_cancel()
        distributor.with_company(distributor.company_id).action_cancel()
        po.with_company(po.company_id).button_cancel()
        self.env.invalidate_all()
        if (central.state != "cancel" or distributor.state != "cancel" or po.state != "cancel"):
            raise UserError(_("Standard cancellation did not close all three orders. Rolled back."))
        refreshed = self.env["stock.move"].browse(list(old_move_ids))
        if any(m.state != "done" for m in refreshed.filtered(lambda m: m.id in done_ids)):
            raise UserError(_("A completed stock move was altered by cancellation. Rolled back."))
        if any(m.state != "cancel" for m in refreshed.filtered(lambda m: m.id not in done_ids)):
            raise UserError(_("Some open stock moves remain active. Rolled back."))
        if any(r.state != "done" for r in now_stock["returns"]):
            raise UserError(_("A completed return changed state. Rolled back."))
        if any(m.state != "posted" for m in reversals_now):
            raise UserError(_("A posted accounting reversal changed state. Rolled back."))
        _logger.info(
            "VINCI_INTERCOMPANY | FINAL_CLOSURE_SUCCESS | CENTRAL=%s | DIST_SO=%s | PO=%s",
            central.name, distributor.name, po.name,
        )
        return {"type": "ir.actions.act_window_close"}
