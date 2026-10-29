"""Package 3A: strictly pre-delivery, pre-invoice intercompany cancellation.

Later delivered/invoiced variants MUST use separate return/reversal workflows.
"""

import logging

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class VinciIntercompanyCancellation(models.TransientModel):
    _inherit = "vinci.intercompany.order.review.wizard"

    def _vinci_validate_safe_cancellation(self, summary):
        self.ensure_one()
        central_so = self.sale_order_id
        po = summary["purchase_orders"]
        distributor_so = summary["counterpart_orders"]
        sales = central_so | distributor_so
        pickings = summary["pickings"]
        moves = pickings.move_ids

        if len(po) != 1 or len(distributor_so) != 1:
            raise UserError(_("Exactly one linked distributor PO and SO are required."))
        if central_so.state != "sale" or distributor_so.state != "sale" or po.state != "purchase":
            raise UserError(_(
                "Cancellation requires confirmed Central SO, Distribution SO "
                "and Distribution PO. Reopen the wizard if a document changed."
            ))
        if summary["sale_invoices"] or summary["purchase_invoices"]:
            raise UserError(_(
                "One or more invoices, credit notes, bills or refunds exist. "
                "Package 3A cannot cancel invoiced documents; "
                "the accounting reversal workflow is not implemented yet."
            ))
        if any(p.state == "done" for p in pickings) or any(m.state == "done" for m in moves):
            raise UserError(_(
                "A linked transfer or stock move is completed. "
                "Returns and partial deliveries are not supported by Package 3A."
            ))
        for sale in sales:
            for line in sale.order_line.filtered(lambda rec: not rec.display_type):
                if float_compare(line.qty_delivered, 0, precision_rounding=line.product_uom.rounding) != 0:
                    raise UserError(_("Delivered quantities exist on %s. Cancellation stopped.") % sale.name)
        for line in po.order_line.filtered(lambda rec: not rec.display_type):
            if float_compare(line.qty_received, 0, precision_rounding=line.product_uom.rounding) != 0:
                raise UserError(_("Received quantities exist on %s. Cancellation stopped.") % po.name)

        # This first version deliberately rejects unusual document/move structures.
        if not moves or not pickings:
            raise UserError(_("No linked stock moves found. Cancellation requires manual review."))
        allowed_move_states = {"draft", "confirmed", "waiting", "assigned", "partially_available", "cancel"}
        for move in moves:
            if move.state not in allowed_move_states:
                raise UserError(_("Unsupported stock move state %s.") % move.state)
            if move.picked or any(move.move_line_ids.mapped("picked")):
                raise UserError(_("Picked stock operations exist. Cancellation stopped."))

        linked_sale_moves = sales.order_line.move_ids
        linked_po_moves = po.order_line.move_ids
        expected_moves = linked_sale_moves | linked_po_moves
        active = moves.filtered(lambda m: m.state != "cancel")
        if set(active.ids) != set(expected_moves.filtered(lambda m: m.state != "cancel").ids):
            raise UserError(_("Unexpected stock moves on linked transfers. Cancellation stopped."))

        external = (active.move_orig_ids | active.move_dest_ids).filtered(
            lambda m: m.state != "cancel" and m not in active
        )
        if external:
            raise UserError(_(
                "Some transfers depend on stock moves outside the three linked orders: %s."
            ) % ", ".join(map(str, external.ids)))

        # Prevent cancelling unrelated transfers that happen to be linked by custom code.
        for picking in pickings:
            if picking.state not in {"draft", "waiting", "confirmed", "assigned", "cancel"}:
                raise UserError(_("Unsupported picking state %s.") % picking.state)
            if picking.company_id not in (central_so.company_id | po.company_id):
                raise UserError(_("An intercompany picking belongs to an unexpected company."))

        return {
            "sales": sales,
            "po": po,
            "pickings": pickings,
            "moves": moves,
            "document_ids": (tuple(sorted(sales.ids)), tuple(sorted(po.ids)),
                             tuple(sorted(pickings.ids)), tuple(sorted(moves.ids))),
        }

    def _vinci_cancel_undelivered_intercompany(self):
        self.ensure_one()
        if self.operation != "cancellation":
            raise UserError(_("Invalid operation for cancellation."))
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_("Only Sales Managers may cancel intercompany documents."))

        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        before = self._vinci_validate_safe_cancellation(summary)

        # Locks all document, move, move-line and picking rows; re-read under lock.
        self._vinci_lock_revision_documents(summary)
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        current = self._vinci_validate_safe_cancellation(summary)
        if before["document_ids"] != current["document_ids"]:
            raise UserError(_("Linked documents changed during cancellation. Reopen the wizard."))

        central_so = self.sale_order_id
        distributor_so = summary["counterpart_orders"]
        distributor_po = summary["purchase_orders"]
        pickings = summary["pickings"]
        original_moves = pickings.move_ids
        _logger.info(
            "VINCI_INTERCOMPANY | CANCELLATION_START | CENTRAL=%s | DIST_SO=%s "
            "| DIST_PO=%s | USER=%s | MOVE_IDS=%s",
            central_so.name, distributor_so.name, distributor_po.name,
            self.env.user.id, original_moves.ids,
        )

        # Standard Odoo business methods, rather than directly setting state.
        # We verify outcomes because custom overrides may instead return a wizard.
        central_so.with_company(central_so.company_id).action_cancel()
        if central_so.state != "cancel":
            raise UserError(_("Central SO was not cancelled by the standard workflow."))
        distributor_so.with_company(distributor_so.company_id).action_cancel()
        if distributor_so.state != "cancel":
            raise UserError(_("Distributor SO was not cancelled by the standard workflow."))
        distributor_po.with_company(distributor_po.company_id).button_cancel()
        if distributor_po.state != "cancel":
            raise UserError(_("Distributor PO was not cancelled by the standard workflow."))

        self.env.invalidate_all()
        for picking in pickings:
            if picking.state != "cancel":
                raise UserError(_(
                    "Transfer %s remained active after cancellation; operation rolled back."
                ) % picking.name)
        for move in original_moves:
            if move.state != "cancel":
                raise UserError(_(
                    "Stock move %s remained active after cancellation; operation rolled back."
                ) % move.id)

        # Odoo's request transaction rolls back on any raised exception.
        _logger.info(
            "VINCI_INTERCOMPANY | CANCELLATION_SUCCESS | CENTRAL=%s | DIST_SO=%s "
            "| DIST_PO=%s | PICKINGS=%s",
            central_so.name, distributor_so.name, distributor_po.name, pickings.ids,
        )
        return {"type": "ir.actions.act_window_close"}
