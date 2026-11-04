"""Pre-confirmation bulk operations, limited to Vinci Food BV documents."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _is_vinci_food_bv(company):
    """Require the actual BV company, not merely any central-company checkbox."""
    return bool(
        company
        and company.is_central_company
        and (company.name or "").strip().casefold() == "vinci food bv"
    )


class VinciBulkSaleOrder(models.Model):
    _inherit = "sale.order"

    vinci_bulk_preconfirm_available = fields.Boolean(
        compute="_compute_vinci_bulk_preconfirm_available"
    )

    @api.depends("company_id", "company_id.name", "company_id.is_central_company", "state")
    def _compute_vinci_bulk_preconfirm_available(self):
        for order in self:
            order.vinci_bulk_preconfirm_available = (
                _is_vinci_food_bv(order.company_id) and order.state in ("draft", "sent")
            )

    def _vinci_open_bulk_document_wizard(self, operation):
        self.ensure_one()
        self._vinci_check_bulk_document_editable()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bulk Route") if operation == "route" else _("Bulk Tax"),
            "res_model": "vinci.bulk.document.line.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_operation": operation,
            },
        }

    def _vinci_check_bulk_document_editable(self):
        self.ensure_one()
        if not _is_vinci_food_bv(self.company_id) or self.state not in ("draft", "sent"):
            raise UserError(_("Bulk changes are only allowed on unconfirmed Vinci Food BV sales orders."))
        if self.env.company != self.company_id:
            raise UserError(_("Switch to Vinci Food BV before applying bulk changes."))
        self.check_access("write")

    def action_vinci_bulk_route(self):
        return self._vinci_open_bulk_document_wizard("route")

    def action_vinci_bulk_tax(self):
        return self._vinci_open_bulk_document_wizard("tax")


class VinciBulkPurchaseOrder(models.Model):
    _inherit = "purchase.order"

    vinci_bulk_preconfirm_available = fields.Boolean(
        compute="_compute_vinci_bulk_preconfirm_available"
    )
    vinci_bulk_purchase_route_available = fields.Boolean(
        compute="_compute_vinci_bulk_purchase_route_available"
    )

    @api.depends("company_id", "company_id.name", "company_id.is_central_company", "state")
    def _compute_vinci_bulk_preconfirm_available(self):
        for order in self:
            order.vinci_bulk_preconfirm_available = (
                _is_vinci_food_bv(order.company_id) and order.state in ("draft", "sent", "to approve")
            )

    def _compute_vinci_bulk_purchase_route_available(self):
        available = "route_id" in self.env["purchase.order.line"]._fields
        for order in self:
            order.vinci_bulk_purchase_route_available = available

    def _vinci_check_bulk_document_editable(self):
        self.ensure_one()
        if not _is_vinci_food_bv(self.company_id) or self.state not in ("draft", "sent", "to approve"):
            raise UserError(_("Bulk changes are only allowed on unconfirmed Vinci Food BV purchase orders."))
        if self.env.company != self.company_id:
            raise UserError(_("Switch to Vinci Food BV before applying bulk changes."))
        self.check_access("write")

    def _vinci_open_bulk_document_wizard(self, operation):
        self.ensure_one()
        self._vinci_check_bulk_document_editable()
        if operation == "route" and "route_id" not in self.env["purchase.order.line"]._fields:
            raise UserError(_("Purchase order lines do not have a route field in this database."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Bulk Route") if operation == "route" else _("Bulk Tax"),
            "res_model": "vinci.bulk.document.line.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_purchase_order_id": self.id,
                "default_operation": operation,
            },
        }

    def action_vinci_bulk_route(self):
        return self._vinci_open_bulk_document_wizard("route")

    def action_vinci_bulk_tax(self):
        return self._vinci_open_bulk_document_wizard("tax")
