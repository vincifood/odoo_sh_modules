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
        operation_label = _("revision") if self.operation == "revision" else _("cancellation")
        raise UserError(_(
            "The read-only review for %(operation)s is ready, but no documents have been changed. "
            "The next development step is to implement and test the %(operation)s workflow "
            "on a staging copy, including stock returns and invoice reversals where required.",
            operation=operation_label,
        ))
