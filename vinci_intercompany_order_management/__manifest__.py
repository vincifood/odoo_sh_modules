{
    "name": "Vinci Intercompany Order Management",
    "version": "18.0.1.1.0",
    "summary": "Safely review and manage linked intercompany order documents",
    "description": """
Review and revise linked intercompany orders. Package 3A supports conservative
cancellation only when all related orders are confirmed and no goods have been
delivered/received or invoiced. Completed transfers and financial reversals
remain blocked until their own verified workflows are implemented.
    """,
    "category": "Sales",
    "author": "Vinci Food - Onur Deniz",
    "license": "LGPL-3",
    "depends": [
        "sale_management",
        "purchase",
        "stock",
        "account",
        "sale_purchase_inter_company_rules",
        "vinci_food_route_planner",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/res_company_views.xml",
        "wizard/intercompany_order_review_wizard_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
