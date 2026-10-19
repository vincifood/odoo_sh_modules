from odoo import _, fields, models
from odoo.exceptions import UserError


class VinciIntercompanyOrderReviewWizard(models.TransientModel):
    _name = "vinci.intercompany.order.review.wizard"
    _description = "Vinci Intercompany Order Document Review"

    sale_order_id = fields.Many2one("sale.order", string="Source Sales Order", readonly=True)
    operation = fields.Selection([
        ("revision", "Revise and Update Documents"),
        ("cancellation", "Cancel and Update Documents"),
    ], string="Requested operation", readonly=True)

    purchase_order_ids = fields.Many2many("purchase.order", string="Linked Purchase Orders", readonly=True)
    counterpart_sale_order_ids = fields.Many2many(
        "sale.order", "vinci_intercompany_review_counterpart_rel",
        "wizard_id", "sale_order_id", string="Counterpart Sales Orders", readonly=True
    )
    sale_invoice_ids = fields.Many2many(
        "account.move", "vinci_intercompany_review_sale_invoice_rel",
        "wizard_id", "move_id", string="Sales Invoices / Credit Notes", readonly=True
    )
    purchase_invoice_ids = fields.Many2many(
        "account.move", "vinci_intercompany_review_purchase_invoice_rel",
        "wizard_id", "move_id", string="Purchase Bills / Refunds", readonly=True
    )
    picking_ids = fields.Many2many(
        "stock.picking", "vinci_intercompany_review_picking_rel",
        "wizard_id", "picking_id", string="Related Transfers / Returns", readonly=True
    )

    revision_line_ids = fields.One2many(
        "vinci.intercompany.revision.line",
        "wizard_id",
        string="Revision Lines",
    )

    def action_confirm(self):
        self.ensure_one()

        if self.operation == "revision":
            result = self._vinci_validate_revision()

            raise UserError(_(
                "Revision validation successful.\n\n"
                "Source Sales Order: %(so)s\n"
                "Matched product lines: %(count)s\n\n"
                "No documents have been modified yet.",
                so=self.sale_order_id.name,
                count=len(result["matches"]),
            ))

        if self.operation == "cancellation":
            self.sale_order_id._vinci_get_intercompany_document_summary()

            raise UserError(_(
                "Cancellation document validation successful.\n\n"
                "Cancellation is not implemented yet. "
                "No documents have been modified."
            ))

        raise UserError(_("Invalid intercompany operation."))

    def _vinci_validate_revision(self):
        self.ensure_one()

        if self.operation != "revision":
            raise UserError(_("This validation is only for revisions."))

        sale_order = self.sale_order_id
        summary = sale_order._vinci_get_intercompany_document_summary()

        purchase_orders = summary["purchase_orders"]
        counterpart_orders = summary["counterpart_orders"]

        # First version: only undelivered and uninvoiced orders.
        all_sale_orders = sale_order | counterpart_orders

        if summary["sale_invoices"] or summary["purchase_invoices"]:
            raise UserError(_(
                "Revision is not yet supported for orders with invoices or bills."
            ))

        if any(
            picking.state == "done"
            for picking in summary["pickings"]
        ):
            raise UserError(_(
                "Revision is not yet supported for completed transfers."
            ))

        if any(
            move.state == "done"
            for move in summary["pickings"].move_ids
        ):
            raise UserError(_(
                "Revision is not yet supported for completed stock moves."
            ))

        if any(
            line.qty_delivered
            for order in all_sale_orders
            for line in order.order_line
            if not line.display_type
        ):
            raise UserError(_(
                "Revision is not yet supported for delivered quantities."
            ))

        if any(
            line.qty_received
            for po in purchase_orders
            for line in po.order_line
            if not line.display_type
        ):
            raise UserError(_(
                "Revision is not yet supported for received quantities."
            ))

        for line in self.revision_line_ids:
            if line.new_quantity < 0:
                raise UserError(_(
                    "New quantity cannot be negative."
                ))

        matches = self._vinci_match_revision_lines(summary)

        if not matches:
            raise UserError(_(
                "No valid revision lines were found."
            ))

        return {
            "summary": summary,
            "matches": matches,
        }


    def _vinci_match_revision_lines(self, summary):
        self.ensure_one()

        central_so = self.sale_order_id
        distributor_po = summary["purchase_orders"]
        distributor_so = summary["counterpart_orders"]

        if len(distributor_po) != 1 or len(distributor_so) != 1:
            raise UserError(_(
                "Exactly one distributor PO and one distributor SO are required."
            ))

        central_lines = central_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        purchase_lines = distributor_po.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        distributor_lines = distributor_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )

        if not (
            len(central_lines)
            == len(purchase_lines)
            == len(distributor_lines)
            == len(self.revision_line_ids)
        ):
            raise UserError(_(
                "The three orders must contain the same number of product lines."
            ))

        matches = []

        if len(set(self.revision_line_ids.mapped("sale_order_line_id").ids)) != len(
                    self.revision_line_ids
                ):
            raise UserError(_(
                "The same sales order line cannot be revised more than once."
            ))

        for revision_line in self.revision_line_ids:
            central_line = revision_line.sale_order_line_id

            if central_line not in central_lines:
                raise UserError(_("Invalid central sales order line."))

            po_matches = purchase_lines.filtered(
                lambda line: line.product_id == central_line.product_id
            )
            so_matches = distributor_lines.filtered(
                lambda line: line.product_id == central_line.product_id
            )

            if len(po_matches) != 1 or len(so_matches) != 1:
                raise UserError(_(
                    "Product %(product)s cannot be matched uniquely "
                    "across the intercompany documents.",
                    product=central_line.product_id.display_name,
                ))

            po_line = po_matches[0]
            distributor_line = so_matches[0]

            if not (
                central_line.product_uom == po_line.product_uom
                == distributor_line.product_uom
            ):
                raise UserError(_(
                    "Units of measure do not match for product %(product)s.",
                    product=central_line.product_id.display_name,
                ))

            if revision_line.product_id != central_line.product_id:
                raise UserError(_("Revision line product does not match."))

            matches.append({
                "central_line": central_line,
                "purchase_line": po_line,
                "distributor_line": distributor_line,
                "new_quantity": revision_line.new_quantity,
            })

        return matches
