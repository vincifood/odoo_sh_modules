from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


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

    original_box_quantity = fields.Float(string="Original Boxes", readonly=True)
    new_box_quantity = fields.Float(string="New Boxes", default=0.0)

    # Internal quantity in product UoM (units), not entered by the user.
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

    product_packaging_id = fields.Many2one(
        "product.packaging",
        string="Box Packaging",
        domain="[('product_id', '=', product_id), ('package_type_id.name', '=', 'Kutu')]",
    )


    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.sale_order_line_id:
                continue

            line.product_packaging_id = False

            if not line.product_id:
                continue

            line.product_uom_id = line.product_id.uom_id
            line.original_quantity = 0.0

            default_packaging = line.product_id.default_product_packaging_id

            if (
                default_packaging
                and default_packaging.product_id == line.product_id
                and default_packaging.package_type_id.name == "Kutu"
            ):
                line.product_packaging_id = default_packaging




    @api.onchange("new_box_quantity", "product_packaging_id")
    def _onchange_box_quantity(self):
        for line in self:
            if line.product_packaging_id and line.product_packaging_id.qty:
                # Packaging qty is expressed in the product base UoM.
                base_qty = line.new_box_quantity * line.product_packaging_id.qty
                line.new_quantity = line.product_id.uom_id._compute_quantity(
                    base_qty, line.product_uom_id or line.product_id.uom_id
                )

    def _vinci_validate_box_quantity(self):
        """Convert editable whole-box counts into internal line quantities."""
        for line in self:
            if not line.product_id:
                raise ValidationError(_("Every revision line needs a product."))
            if line.new_box_quantity < 0:
                raise ValidationError(_("Box count cannot be negative."))
            if line.sale_order_line_id and line.product_id != line.sale_order_line_id.product_id:
                raise ValidationError(_("Existing product cannot be changed."))
            if not line.sale_order_line_id and line.product_uom_id != line.product_id.uom_id:
                raise ValidationError(_("New products must use the base unit of measure."))

            packaging = line.product_packaging_id
            if not packaging:
                # Legacy rows without Kutu packaging can remain UNCHANGED.
                if line.sale_order_line_id and float_compare(
                    line.new_quantity, line.original_quantity,
                    precision_rounding=line.product_uom_id.rounding,
                ) == 0 and line.new_box_quantity == 0:
                    continue
                raise ValidationError(_(
                    "Select a Kutu packaging for product %s."
                ) % line.product_id.display_name)

            if packaging.product_id != line.product_id or packaging.package_type_id.name != "Kutu":
                raise ValidationError(_("Choose the product's Kutu packaging."))
            if packaging.qty <= 0:
                raise ValidationError(_("Kutu packaging quantity must be positive."))
            if packaging.company_id and packaging.company_id not in (
                line.wizard_id.sale_order_id.company_id,
                line.wizard_id.purchase_order_ids[:1].company_id,
            ):
                raise ValidationError(_("Kutu packaging belongs to another company."))

            if float_compare(line.new_box_quantity, round(line.new_box_quantity),
                             precision_digits=6) != 0:
                raise ValidationError(_(
                    "Product %(product)s requires a whole number of boxes. "
                    "One box contains %(qty)s units."
                ) % {"product": line.product_id.display_name, "qty": packaging.qty})

            base_qty = line.new_box_quantity * packaging.qty
            new_units = line.product_id.uom_id._compute_quantity(
                base_qty, line.product_uom_id
            )
            if not line.sale_order_line_id and float_compare(
                new_units, 0, precision_rounding=line.product_uom_id.rounding
            ) <= 0:
                raise ValidationError(_("New products must have at least one box."))

            # Write the internal unit quantity for the revision engine.
            line.new_quantity = new_units
