import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _create_or_update_picking(self):
        if self.env.context.get("vinci_skip_purchase_picking_update"):
            _logger.info(
                "VINCI_INTERCOMPANY | SKIP_PURCHASE_PICKING_UPDATE | "
                "PO_LINES=%s",
                self.ids,
            )
            return None

        return super()._create_or_update_picking()
