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
        "auto_purchase_order_id",
    )
    def _compute_vinci_can_review_intercompany(self):
        for order in self:
            order.vinci_can_review_intercompany = bool(
                order.company_id.is_central_company
                and order.auto_purchase_order_id
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

        if not distributor_company.is_distributor:
            raise UserError(_("The source PO does not belong to a distributor."))

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

        wizard = self.env["vinci.intercompany.order.review.wizard"].create({
            "sale_order_id": self.id,
            "operation": operation,
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
    