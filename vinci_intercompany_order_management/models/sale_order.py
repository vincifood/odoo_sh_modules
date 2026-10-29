from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    vinci_can_review_intercompany = fields.Boolean(
        string="Can Review Intercompany Documents",
        compute="_compute_vinci_can_review_intercompany",
    )

    @api.depends(
        "company_id",
        "company_id.is_central_company",
        "company_id.vinci_intercompany_management_enabled",
        "auto_purchase_order_id",
        "auto_purchase_order_id.auto_sale_order_id",
        "auto_purchase_order_id.auto_sale_order_id.order_type",
    )

    def _compute_vinci_can_review_intercompany(self):
        for order in self:
            central_company = order.company_id
            purchase_order = order.auto_purchase_order_id

            counterpart_order = (
                purchase_order.auto_sale_order_id
                if purchase_order
                else self.env["sale.order"]
            )

            # Some mobile API orders have no explicit SO link.
            if (
                purchase_order
                and not counterpart_order
                and purchase_order.origin
            ):
                counterpart_order = self.env["sale.order"].search([
                    ("name", "=", purchase_order.origin),
                    ("company_id", "=", purchase_order.company_id.id),
                ], limit=2)

            order.vinci_can_review_intercompany = bool(
                central_company.is_central_company
                and central_company.vinci_intercompany_management_enabled
                and purchase_order
                and len(counterpart_order) == 1
                and counterpart_order.order_type == "cold_sale"
            )

    def _vinci_get_intercompany_document_summary(self):
        """Find linked intercompany documents without modifying them."""
        self.ensure_one()

        SaleOrder = self.env["sale.order"]
        PurchaseOrder = self.env["purchase.order"]

        central_company = self.company_id

        if not central_company.is_central_company:
            raise UserError(_(
                "This operation is only available for central company sales orders."
            ))

        if self.env.company != central_company:
            raise UserError(_(
                "Please switch to the sales order's central company."
            ))

        if not self.auto_purchase_order_id:
            raise UserError(_(
                "This sales order has no directly linked source purchase order."
            ))

        purchase_order = self.auto_purchase_order_id
        distributor_company = purchase_order.company_id

        if not (
            central_company.vinci_intercompany_management_enabled
            and distributor_company.vinci_intercompany_management_enabled
        ):
            raise UserError(_(
                "Intercompany revision and cancellation are not enabled "
                "for both companies."
            ))

        if not distributor_company.is_distributor:
            raise UserError(_("The source PO does not belong to a distributor."))

        if central_company.vinci_intercompany_managed_distributor_id != distributor_company:
            raise UserError(_(
                "This distributor is not authorized for intercompany "
                "revision and cancellation with the central company."
            ))

        if distributor_company.central_vendor_company_id != central_company:
            raise UserError(_(
                "The distributor's central vendor company does not match."
            ))

        # Use the explicit SO link where available.
        counterpart_orders = purchase_order.auto_sale_order_id

        # The custom API may create POs without auto_sale_order_id.
        if not counterpart_orders:
            counterpart_orders = SaleOrder.search([
                ("name", "=", purchase_order.origin),
                ("company_id", "=", distributor_company.id),
            ])

        if len(counterpart_orders) != 1:
            raise UserError(_(
                "Exactly one distributor sales order must be linked to this PO."
            ))

        if counterpart_orders.company_id != distributor_company:
            raise UserError(_("The linked sales order belongs to another company."))

        # Only Cold Sale orders are supported.
        # Hot Sale and other order types must never be revised or cancelled.
        if counterpart_orders.order_type != "cold_sale":
            raise UserError(_(
                "Intercompany revision and cancellation are only allowed "
                "for Cold Sale orders. The linked distributor sales order "
                "is not a Cold Sale."
            ))

        if (
            purchase_order.origin
            and purchase_order.origin != counterpart_orders.name
        ):
            raise UserError(_("The PO origin does not match the distributor SO."))

        all_sale_orders = self | counterpart_orders

        return {
            "purchase_orders": purchase_order,
            "counterpart_orders": counterpart_orders,
            "sale_invoices": all_sale_orders.invoice_ids,
            "purchase_invoices": purchase_order.invoice_ids,
            "pickings": (
                all_sale_orders.picking_ids
                | purchase_order.picking_ids
            ),
        }


    def action_vinci_review_intercompany_revision(self):
        return self._vinci_open_intercompany_review("revision")

    def action_vinci_review_intercompany_cancellation(self):
        return self._vinci_open_intercompany_review("cancellation")

    def _vinci_open_intercompany_review(self, operation):
        self.ensure_one()
        summary = self._vinci_get_intercompany_document_summary()

        revision_lines = []
        original_snapshot = {}

        if operation == "revision":
            for line in self.order_line:
                if line.display_type or not line.product_id or line.product_uom_qty <= 0:
                    continue

                original_snapshot[str(line.id)] = line.product_uom_qty
                packaging = line.product_packaging_id
                if not packaging or packaging.package_type_id.name != "Kutu":
                    candidate = line.product_id.default_product_packaging_id
                    if candidate and candidate.package_type_id.name == "Kutu":
                        packaging = candidate
                    else:
                        packaging = False

                original_boxes = 0.0
                if packaging and packaging.qty > 0:
                    base_qty = line.product_uom._compute_quantity(
                        line.product_uom_qty, line.product_id.uom_id
                    )
                    original_boxes = base_qty / packaging.qty
                    # Legacy non-whole quantities must not be rounded silently.
                    if abs(original_boxes - round(original_boxes)) > 1e-6:
                        packaging = False
                        original_boxes = 0.0

                revision_lines.append((0, 0, {
                    "sale_order_line_id": line.id,
                    "product_id": line.product_id.id,
                    "original_quantity": line.product_uom_qty,
                    "new_quantity": line.product_uom_qty,
                    "original_box_quantity": original_boxes,
                    "new_box_quantity": original_boxes,
                    "product_uom_id": line.product_uom.id,
                    "product_packaging_id": packaging.id if packaging else False,
                }))

        wizard = self.env["vinci.intercompany.order.review.wizard"].create({
            "sale_order_id": self.id,
            "operation": operation,
            "original_line_snapshot": original_snapshot,
            "revision_line_ids": revision_lines,
            "purchase_order_ids": [
                (6, 0, summary["purchase_orders"].ids)
            ],
            "counterpart_sale_order_ids": [
                (6, 0, summary["counterpart_orders"].ids)
            ],
            "sale_invoice_ids": [
                (6, 0, summary["sale_invoices"].ids)
            ],
            "purchase_invoice_ids": [
                (6, 0, summary["purchase_invoices"].ids)
            ],
            "picking_ids": [
                (6, 0, summary["pickings"].ids)
            ],
        })

        return {
            "type": "ir.actions.act_window",
            "name": _("Intercompany document review"),
            "res_model": "vinci.intercompany.order.review.wizard",
            "view_mode": "form",
            "res_id": wizard.id,
            "target": "new",
        }
