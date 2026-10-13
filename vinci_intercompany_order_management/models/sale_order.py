from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _vinci_get_intercompany_document_summary(self):
        """Return a read-only summary of linked intercompany documents.

        The PO origin / auto_purchase_order_id relationship mirrors the
        relationship used by the existing Vinci API. This is discovery logic
        only; it does not change or cancel any business document.
        """
        self.ensure_one()
        PurchaseOrder = self.env["purchase.order"]
        SaleOrder = self.env["sale.order"]

        purchase_orders = PurchaseOrder.search([("origin", "=", self.name)])
        counterpart_orders = self.env["sale.order"]
        if purchase_orders and "auto_purchase_order_id" in SaleOrder._fields:
            counterpart_orders = SaleOrder.search([
                ("auto_purchase_order_id", "in", purchase_orders.ids)
            ])

        all_sale_orders = self | counterpart_orders
        sale_invoices = all_sale_orders.invoice_ids
        purchase_invoices = purchase_orders.invoice_ids

        pickings = (
            all_sale_orders.picking_ids
            | purchase_orders.picking_ids
        )

        return {
            "purchase_orders": purchase_orders,
            "counterpart_orders": counterpart_orders,
            "sale_invoices": sale_invoices,
            "purchase_invoices": purchase_invoices,
            "pickings": pickings,
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
            "purchase_order_ids": [(6, 0, summary["purchase_orders"].ids)],
            "counterpart_sale_order_ids": [(6, 0, summary["counterpart_orders"].ids)],
            "sale_invoice_ids": [(6, 0, summary["sale_invoices"].ids)],
            "purchase_invoice_ids": [(6, 0, summary["purchase_invoices"].ids)],
            "picking_ids": [(6, 0, summary["pickings"].ids)],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Intercompany document review"),
            "res_model": "vinci.intercompany.order.review.wizard",
            "view_mode": "form",
            "res_id": wizard.id,
            "target": "new",
        }
