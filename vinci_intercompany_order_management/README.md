# Vinci Intercompany Order Management — initial foundation

## What this version does
- Adds two buttons to the Sales Order form.
- Opens a read-only review wizard showing detected linked purchase orders,
  counterpart sales orders, invoices/bills, and stock transfers.
- Uses the clicked Sales Order as the source, so the workflow can be initiated
  from either intercompany side.
- Does **not** modify, cancel, post, return, or delete any business documents.

## Important first-test notes
- The initial link discovery uses purchase order `origin = sales order name`
  and counterpart sales order `auto_purchase_order_id = purchase order`.
- These links must be verified against actual intercompany orders in a staging
  database before implementing changes.
- Revision/cancellation implementation is intentionally not enabled yet.
- Install/test only on a staging or restored test database first.

## Install
Copy this folder into an addons path, update the Apps list, then install
"Vinci Intercompany Order Management".
