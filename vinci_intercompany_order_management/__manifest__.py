{
    "name": "Vinci Intercompany Order Management",
    "version": "18.0.1.0.0",
    "summary": "Safely review and manage linked intercompany order documents",
    "description": """
Initial foundation for reviewing intercompany sales/purchase orders and their
related invoices and stock transfers. The first version intentionally performs
a read-only preview; revision/cancellation operations will be added after the
links and business rules are verified in a test database.
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
