from odoo import api, fields, models


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




    def _vinci_validate_box_quantity(self):
        """Validate whole-box quantities using the selected packaging."""
        from odoo.exceptions import ValidationError
        from odoo.tools.float_utils import float_compare

        for line in self:
            if not line.product_id:
                raise ValidationError(
                    "Every revision line must have a product."
                )

            # New products must have a positive quantity.
            # Zero is reserved for removing existing products.
            if not line.sale_order_line_id and line.new_quantity <= 0:
                raise ValidationError(
                    "New product %s must have a quantity greater than zero."
                    % line.product_id.display_name
                )

            # Zero quantity on existing lines means product removal.
            if line.new_quantity == 0:
                continue

            # Validate the unit of measure for newly added products.
            if (
                not line.sale_order_line_id
                and line.product_uom_id != line.product_id.uom_id
            ):
                raise ValidationError(
                    "New product %s must use its default unit of measure."
                    % line.product_id.display_name
                )

            if line.new_quantity < 0:
                raise ValidationError(
                    "New quantity cannot be negative."
                )


            # Preserve unchanged legacy order lines without box packaging.
            if (
                line.sale_order_line_id
                and not line.product_packaging_id
                and float_compare(
                    line.new_quantity,
                    line.original_quantity,
                    precision_rounding=line.product_uom_id.rounding,
                ) == 0
            ):
                continue


            packaging = line.product_packaging_id

            if not packaging:
                raise ValidationError(
                    "Please select Box Packaging for product %s."
                    % line.product_id.display_name
                )

            if packaging.product_id != line.product_id:
                raise ValidationError(
                    "Selected packaging does not belong to product %s."
                    % line.product_id.display_name
                )

            if packaging.package_type_id.name != "Kutu":
                raise ValidationError(
                    "Only Box (Kutu) packaging is allowed for product %s."
                    % line.product_id.display_name
                )

            company = line.wizard_id.sale_order_id.company_id
            if packaging.company_id and packaging.company_id != company:
                raise ValidationError(
                    "Selected packaging is not available for company %s."
                    % company.display_name
                )

            if packaging.qty <= 0:
                raise ValidationError(
                    "Box quantity must be greater than zero for product %s."
                    % line.product_id.display_name
                )

            box_count = line.new_quantity / packaging.qty

            if float_compare(
                box_count,
                round(box_count),
                precision_digits=6,
            ) != 0:
                raise ValidationError(
                    "Product %s must be ordered in whole boxes. "
                    "One box contains %s units."
                    % (line.product_id.display_name, packaging.qty)
                )
