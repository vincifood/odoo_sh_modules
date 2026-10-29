"""Package 3C: prepare draft financial reversals only after stock returns.

No posting, invoice reconciliation or order cancellation occurs here. Accountants
must review and post the draft credit notes / vendor refunds manually.
"""
import logging

from psycopg2 import errors
from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class VinciIntercompanyFinancialReversal(models.TransientModel):
    _inherit = "vinci.intercompany.order.review.wizard"

    def _vinci_get_accounting_sources(self, summary):
        self.ensure_one()
        central = self.sale_order_id
        distributor = summary["counterpart_orders"]
        po = summary["purchase_orders"]
        if len(distributor) != 1 or len(po) != 1:
            raise UserError(_("Exactly one distributor sales and purchase order is required."))
        if central.state != "sale" or distributor.state != "sale" or po.state != "purchase":
            raise UserError(_("Linked orders changed state. Reopen the cancellation wizard."))
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_("Only Sales Managers can request intercompany reversals."))
        if not self.env.user.has_group("account.group_account_invoice"):
            raise UserError(_("Invoice/Billing access is required to prepare credit notes."))

        # Inspect each document separately to avoid attributing a vendor bill
        # to a different sales company, or accidentally reversing refunds.
        groups = (
            (central.invoice_ids, central.company_id, "out_invoice", central.name, central.order_line),
            (distributor.invoice_ids, distributor.company_id, "out_invoice", distributor.name, distributor.order_line),
            (po.invoice_ids, po.company_id, "in_invoice", po.name, po.order_line),
        )
        sources = self.env["account.move"]
        for invoices, company, expected_type, document_name, order_lines in groups:
            for inv in invoices:
                if inv.company_id != company:
                    raise UserError(_("Unexpected invoice company for %s.") % document_name)
                if inv.move_type not in (expected_type, "out_refund" if expected_type == "out_invoice" else "in_refund"):
                    raise UserError(_("Unsupported accounting document linked to %s.") % document_name)
                if inv.move_type != expected_type:
                    # An existing credit/refund must be manually reviewed. No
                    # attempt to issue a second full credit is permitted.
                    raise UserError(_("A credit note or vendor refund already belongs to %s. Manual review is required.") % document_name)
                if inv.state != "posted":
                    raise UserError(_("Invoice %s is not posted. Handle draft/cancelled invoices manually.") % inv.display_name)
                if inv.reversed_entry_id:
                    raise UserError(_("Invoice %s is itself a reversal.") % inv.display_name)
                # Refuse consolidated invoices or unrelated manual invoice
                # lines: reversing a whole invoice would otherwise credit
                # goods belonging to another customer order.
                product_lines = inv.invoice_line_ids.filtered(
                    lambda line: line.display_type == "product"
                )
                if not product_lines:
                    raise UserError(_("No product lines found on invoice %s.") % inv.display_name)
                for account_line in product_lines:
                    linked = (account_line.sale_line_ids if expected_type == "out_invoice"
                              else account_line.purchase_line_id)
                    if not linked or (linked - order_lines):
                        raise UserError(_(
                            "Invoice %s has unlinked or shared product lines. "
                            "Manual credit note required."
                        ) % inv.display_name)
                sources |= inv

        if not sources:
            raise UserError(_("No posted invoices or bills found for financial reversal."))
        if len(sources) != sum(len(g[0]) for g in groups):
            raise UserError(_("An invoice is linked to more than one document. Manual review required."))
        for inv in sources:
            if inv.company_id not in self.env.user.company_ids:
                raise UserError(_("You do not have access to invoice company %s.") % inv.company_id.name)
            # Includes even draft/cancelled refunds and any historical reversal.
            existing = self.env["account.move"].search_count([
                ("reversed_entry_id", "=", inv.id),
            ])
            if existing:
                raise UserError(_("Invoice %s already has a reversal. No duplicate created.") % inv.display_name)
            if inv.payment_state in ("paid", "in_payment", "partial", "reversed"):
                raise UserError(_("Invoice %s has a payment or reconciliation. Accountant review required.") % inv.display_name)
        return sources

    def _vinci_check_completed_returns(self, summary):
        """Require validated, fully matched returns for all completed stock moves."""
        pickings = summary["pickings"]
        moves = pickings.move_ids
        expected_moves = (self.sale_order_id.order_line.move_ids
                          | summary["counterpart_orders"].order_line.move_ids
                          | summary["purchase_orders"].order_line.move_ids)
        if set(moves.ids) != set(expected_moves.ids):
            raise UserError(_("Unexpected or missing stock moves. Manual review required."))
        active = moves.filtered(lambda m: m.state not in ("done", "cancel"))
        if any(m.picked or any(m.move_line_ids.mapped("picked")) for m in active):
            raise UserError(_("Open stock operations were picked. Manual review required."))
        for move in moves.filtered(lambda m: m.state == "done"):
            if move.product_id.tracking != "none":
                raise UserError(_("Tracked product returns need manual validation: %s.") % move.product_id.display_name)
            returns = self.env["stock.move"].search([
                ("origin_returned_move_id", "=", move.id),
            ])
            if not returns or any(r.state != "done" for r in returns):
                raise UserError(_("Stock return for %s has not been fully validated.") % move.picking_id.name)
            returned_qty = sum(r.product_uom._compute_quantity(r.quantity, move.product_uom) for r in returns)
            if float_compare(returned_qty, move.quantity, precision_rounding=move.product_uom.rounding) != 0:
                raise UserError(_("Returned quantity does not match completed delivery on %s.") % move.picking_id.name)
            if any(r.company_id != move.company_id for r in returns):
                raise UserError(_("Return company mismatch for %s.") % move.picking_id.name)
        return (tuple(sorted(pickings.ids)), tuple(sorted(moves.ids)))

    def _vinci_prepare_financial_reversals(self):
        """Create *draft* credit notes/vendor refunds, never auto-post them."""
        self.ensure_one()
        if self.operation != "cancellation":
            raise UserError(_("Financial reversal requires the cancellation operation."))
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        sources = self._vinci_get_accounting_sources(summary)
        stock_snapshot = self._vinci_check_completed_returns(summary)
        invoice_ids = tuple(sorted(sources.ids))
        # Lock orders and original stock first, then invoice rows. Race checks
        # are repeated after lock acquisition before making any entries.
        self._vinci_lock_revision_documents(summary)
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM account_move WHERE id = ANY(%s) ORDER BY id FOR UPDATE NOWAIT",
                    (list(invoice_ids),),
                )
        except errors.LockNotAvailable:
            raise UserError(_("An accountant is editing one of these invoices. Retry shortly."))
        self.env.invalidate_all()
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        sources = self._vinci_get_accounting_sources(summary)
        if invoice_ids != tuple(sorted(sources.ids)) or stock_snapshot != self._vinci_check_completed_returns(summary):
            raise UserError(_("Linked stock or invoice documents changed. Reopen the wizard."))
        drafts = self.env["account.move"]
        for invoice in sources.sorted(key=lambda m: (m.company_id.id, m.id)):
            reverse_defaults = [{
                "date": fields.Date.context_today(self),
                "ref": _("Intercompany cancellation draft / %s") % invoice.name,
            }]
            reversal = invoice.with_company(invoice.company_id)._reverse_moves(
                default_values_list=reverse_defaults, cancel=False
            )
            expected_type = "out_refund" if invoice.move_type == "out_invoice" else "in_refund"
            if (len(reversal) != 1 or reversal.state != "draft"
                    or reversal.move_type != expected_type
                    or reversal.reversed_entry_id != invoice
                    or reversal.company_id != invoice.company_id):
                raise UserError(_("Unexpected credit-note result for invoice %s. Rolled back.") % invoice.name)
            drafts |= reversal
            _logger.info(
                "VINCI_INTERCOMPANY | ACCOUNTING_DRAFT_CREATED | SO=%s | ORIGINAL=%s | REVERSAL=%s | COMPANY=%s",
                self.sale_order_id.name, invoice.id, reversal.id, invoice.company_id.id,
            )
        # Original orders and stock remain unchanged. Finance users explicitly
        # review the drafts and coordinate final closure after reconciliation.
        return {
            "type": "ir.actions.act_window",
            "name": _("Draft intercompany credit notes / vendor refunds — accountant review"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", drafts.ids)],
            "target": "current",
            "context": {"create": False},
        }
