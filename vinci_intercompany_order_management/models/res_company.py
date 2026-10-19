from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    vinci_intercompany_management_enabled = fields.Boolean(
        string="Enable Intercompany Revision & Cancellation",
        default=False,
        help="Allow this company to participate in the Vinci intercompany revision and cancellation workflow.",
    )

    vinci_intercompany_managed_distributor_id = fields.Many2one(
        "res.company",
        string="Authorized Intercompany Distributor",
        domain="[('is_distributor', '=', True)]",
        help=(
            "The only distributor authorized for intercompany "
            "revision and cancellation with this central company."
        ),
    )
