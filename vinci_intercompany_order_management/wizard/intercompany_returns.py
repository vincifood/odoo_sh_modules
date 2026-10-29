"""Package 3B: prepare physical returns for completed intercompany transfers.

Important: this step never completes a return, cancels an already-delivered
order, or posts accounting entries. Warehouse staff must verify actual goods.
"""

import logging

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class VinciIntercompanyReturns(models.TransientModel):
    _inherit = "vinci.intercompany.order.review.wizard"

    def _vinci_validate_return_preparation(self, summary):
        self.ensure_one()
        central = self.sale_order_id
        po = summary["purchase_orders"]
        distributor = summary["counterpart_orders"]
        pickings = summary["pickings"]
        sales = central | distributor

        if self.operation != "cancellation":
            raise UserError(_("Return preparation requires the cancellation operation."))
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_("Only Sales Managers may prepare intercompany returns."))
        if len(po) != 1 or len(distributor) != 1:
            raise UserError(_("Exactly one distributor PO and SO are required."))
        if (central.state != "sale" or distributor.state != "sale"
                or po.state != "purchase"):
            raise UserError(_("All three orders must remain confirmed. Reopen the wizard."))
        if summary["sale_invoices"] or summary["purchase_invoices"]:
            raise UserError(_(
                "Invoices, credit notes or bills exist. Package 3B cannot process "
                "these orders until the accounting workflow is implemented."
            ))
        if not pickings:
            raise UserError(_("No stock transfers were linked to the three orders."))

        expected_moves = sales.order_line.move_ids | po.order_line.move_ids
        linked_moves = pickings.move_ids
        if set(linked_moves.ids) != set(expected_moves.ids):
            raise UserError(_(
                "Stock transfers contain unrelated moves or linked moves are missing. "
                "Manual review is required."
            ))
        if any(p.company_id not in (central.company_id | po.company_id)
               for p in pickings):
            raise UserError(_("A linked transfer belongs to an unexpected company."))
        if any(p.state not in ("done", "cancel", "assigned", "confirmed", "waiting", "draft")
               for p in pickings):
            raise UserError(_("Unsupported stock transfer state."))
        if any(m.state not in ("done", "cancel", "assigned", "confirmed", "waiting", "draft", "partially_available")
               for m in linked_moves):
            raise UserError(_("Unsupported stock move state."))
        if any(m.picked or any(m.move_line_ids.mapped("picked"))
               for m in linked_moves.filtered(lambda m: m.state not in ("done", "cancel"))):
            raise UserError(_("An unfinished transfer contains picked operations. Manual review required."))

        # Never attempt to create a second return for a completed picking.
        # Include even cancelled returns: a previously attempted return needs review.
        done_pickings = pickings.filtered(lambda p: p.state == "done")
        if not done_pickings:
            raise UserError(_("No completed transfers were found. Use Package 3A for undelivered orders."))
        for picking in done_pickings:
            if not picking._can_return():
                raise UserError(_("Transfer %s cannot be returned using the standard Odoo wizard.") % picking.name)
            if self.env["stock.picking"].search_count([("return_id", "=", picking.id)]):
                raise UserError(_(
                    "A return already exists for transfer %s. Review it manually "
                    "before attempting another intercompany operation."
                ) % picking.name)
            if self.env["stock.move"].search_count([
                ("origin_returned_move_id", "in", picking.move_ids.ids),
            ]):
                raise UserError(_(
                    "Some moves of transfer %s have already been returned. "
                    "The operation was stopped to avoid duplicate returns."
                ) % picking.name)
            for move in picking.move_ids.filtered(lambda m: m.state == "done"):
                if move.scrapped:
                    raise UserError(_("Transfer %s contains scrapped stock. Manual review required.") % picking.name)
                if move.product_id.tracking != "none":
                    # Serial/lot quantities require warehouse-specific return lines.
                    raise UserError(_(
                        "Product %s uses lot/serial tracking. Complete its return manually "
                        "to preserve lot traceability."
                    ) % move.product_id.display_name)
                qty = move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id)
                if float_compare(qty, 0, precision_rounding=move.product_id.uom_id.rounding) <= 0:
                    raise UserError(_("Completed move %s has no positive delivered quantity.") % move.id)

        # Prevent unwanted third-party chain adjustments by Odoo's return wizard.
        active = linked_moves.filtered(lambda m: m.state != "cancel")
        external = (active.move_orig_ids | active.move_dest_ids).filtered(
            lambda m: m.state != "cancel" and m not in active
        )
        if external:
            raise UserError(_(
                "Linked stock moves have external dependencies: %s. "
                "Manual return required."
            ) % ", ".join(map(str, external.ids)))

        return {
            "done_pickings": done_pickings.sorted(key=lambda p: p.id),
            "document_ids": (tuple(sorted(sales.ids)), tuple(sorted(po.ids)),
                             tuple(sorted(pickings.ids)), tuple(sorted(linked_moves.ids))),
        }

    def _vinci_prepare_delivery_returns(self):
        """Create *unvalidated* return pickings; do not cancel original orders."""
        self.ensure_one()
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        before = self._vinci_validate_return_preparation(summary)
        self._vinci_lock_revision_documents(summary)
        summary = self.sale_order_id._vinci_get_intercompany_document_summary()
        after = self._vinci_validate_return_preparation(summary)
        if before["document_ids"] != after["document_ids"]:
            raise UserError(_("Linked documents changed. Reopen the cancellation wizard."))

        created = self.env["stock.picking"]
        for picking in after["done_pickings"]:
            ReturnWizard = self.env["stock.return.picking"].with_company(
                picking.company_id
            ).with_context(
                active_model="stock.picking", active_id=picking.id,
                active_ids=[picking.id]
            )
            return_wizard = ReturnWizard.create({"picking_id": picking.id})
            # Odoo 18 initially sets all return quantities to zero. Explicitly
            # set each original completed move's actually processed quantity.
            seen_move_ids = set()
            for return_line in return_wizard.product_return_moves:
                move = return_line.move_id
                if move.state != "done" or move.id in seen_move_ids:
                    raise UserError(_("Invalid return wizard lines for transfer %s.") % picking.name)
                seen_move_ids.add(move.id)
                qty = move.product_uom._compute_quantity(
                    move.quantity, move.product_id.uom_id
                )
                if float_compare(qty, 0, precision_rounding=move.product_id.uom_id.rounding) <= 0:
                    raise UserError(_("Return quantity must be positive on transfer %s.") % picking.name)
                return_line.quantity = qty
            expected_move_ids = set(picking.move_ids.filtered(lambda m: m.state == "done").ids)
            if not seen_move_ids or seen_move_ids != expected_move_ids:
                raise UserError(_(
                    "The return wizard does not cover every completed move on %s. "
                    "No return was prepared."
                ) % picking.name)

            action = return_wizard.action_create_returns()
            new_picking_id = action.get("res_id") if isinstance(action, dict) else False
            new_picking = self.env["stock.picking"].browse(new_picking_id).exists()
            if not new_picking or new_picking.return_id != picking:
                raise UserError(_("Odoo did not create the expected return for %s.") % picking.name)
            if new_picking.company_id != picking.company_id:
                raise UserError(_("The return company does not match %s.") % picking.name)
            if new_picking.state == "done":
                raise UserError(_("Return %s was completed unexpectedly; transaction rolled back.") % new_picking.name)
            if set(new_picking.move_ids.mapped("origin_returned_move_id").ids) != expected_move_ids:
                raise UserError(_("Returned move linkage is incomplete for %s.") % picking.name)
            created |= new_picking
            _logger.info(
                "VINCI_INTERCOMPANY | RETURN_PREPARED | CENTRAL=%s | ORIGINAL=%s "
                "| RETURN=%s | COMPANY=%s",
                self.sale_order_id.name, picking.name, new_picking.name, picking.company_id.name,
            )

        if not created:
            raise UserError(_("No return transfers were prepared."))

        # Preparing a picking is not a physical return. The user must validate
        # each returned transfer, and cancellation must be performed separately.
        return {
            "type": "ir.actions.act_window",
            "name": _("Intercompany returns awaiting warehouse validation"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
            "target": "current",
            "context": {"create": False},
        }
