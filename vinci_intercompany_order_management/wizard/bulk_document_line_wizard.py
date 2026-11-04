"""Bulk route/tax choice, persisted only when the user clicks Apply."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class VinciBulkDocumentLineWizard(models.TransientModel):
    _name = "vinci.bulk.document.line.wizard"
    _description = "Vinci Food BV Pre-confirmation Bulk Line Edit"

    operation = fields.Selection(
        [("route", "Bulk Route"), ("tax", "Bulk Tax")], required=True, readonly=True
    )
    sale_order_id = fields.Many2one("sale.order", readonly=True)
    purchase_order_id = fields.Many2one("purchase.order", readonly=True)
    route_id = fields.Many2one("stock.route", string="Route")
    tax_id = fields.Many2one("account.tax", string="Tax")

    def action_apply(self):
        self.ensure_one()
        if bool(self.sale_order_id) == bool(self.purchase_order_id):
            raise UserError(_("Select exactly one sales or purchase document."))
        if self.operation not in ("route", "tax"):
            raise UserError(_("Unsupported bulk operation."))

        order = self.sale_order_id or self.purchase_order_id
        order._vinci_check_bulk_document_editable()
        if self.sale_order_id:
            lines = order.order_line.filtered(lambda line: not line.display_type and bool(line.product_id))
        else:
            lines = order.order_line.filtered(lambda line: not line.display_type and bool(line.product_id))
        if not lines:
            raise UserError(_("The document has no product lines to update."))

        if self.operation == "route":
            if not self.route_id:
                raise UserError(_("Choose a route."))
            if self.route_id.company_id and self.route_id.company_id != order.company_id:
                raise UserError(_("The selected route belongs to another company."))
            if "route_id" not in lines._fields:
                raise UserError(_("This document's product lines do not support routes."))
            if self.sale_order_id and not self.route_id.sale_selectable:
                raise UserError(_("Choose a route selectable on sales orders."))
            lines.write({"route_id": self.route_id.id})
        else:
            if not self.tax_id:
                raise UserError(_("Choose a tax."))
            if self.tax_id.company_id != order.company_id:
                raise UserError(_("The selected tax belongs to another company."))
            expected_use = "sale" if self.sale_order_id else "purchase"
            if self.tax_id.type_tax_use not in (expected_use, "none"):
                raise UserError(_("Choose a tax suitable for this document type."))
            field_name = "tax_id" if self.sale_order_id else "taxes_id"
            lines.write({field_name: [(6, 0, [self.tax_id.id])]})

        return {"type": "ir.actions.act_window_close"}
