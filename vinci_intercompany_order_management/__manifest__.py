{
    "name": "Vinci Intercompany Order Management",
    "version": "18.0.1.4.0",
    "summary": "Safely review and manage linked intercompany order documents",
    "description": """
Package 2 product revision, Package 3A safe pre-delivery cancellation,
Package 3B preparation of unvalidated physical stock returns, and Package 3C
preparation of draft invoice reversals after validated stock returns.
Package 3D finalizes linked order cancellation only after physical returns
and applicable credit notes / vendor refunds have been posted.
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
