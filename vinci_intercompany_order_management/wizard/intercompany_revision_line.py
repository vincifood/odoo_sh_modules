from odoo import fields, models


class VinciIntercompanyRevisionLine(models.TransientModel):
    _name = "vinci.intercompany.revision.line"
    _description = "Intercompany Sales Order Revision Line"

    wizard_id = fields.Many2one(
        "vinci.intercompany.order.review.wizard",
        string="Revision Wizard",
        required=True,
        ondelete="cascade",
    )

    sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Original Sales Order Line",
        readonly=True,
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
    )

    original_quantity = fields.Float(
        string="Original Quantity",
        readonly=True,
    )

    new_quantity = fields.Float(
        string="New Quantity",
        required=True,
        default=0.0,
    )

    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        required=True,
    )
