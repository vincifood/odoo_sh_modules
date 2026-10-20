import logging

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare
from psycopg2 import errors

_logger = logging.getLogger(__name__)


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

        if self.operation == "revision":
            _logger.info(
                "VINCI_INTERCOMPANY | REVISION_START | SO=%s | USER=%s",
                self.sale_order_id.name,
                self.env.user.id,
            )

            try:
                result = self._vinci_validate_revision()
                self._vinci_apply_quantity_revision(result)
            except Exception:
                _logger.exception(
                    "VINCI_INTERCOMPANY | REVISION_FAILED | SO=%s",
                    self.sale_order_id.name,
                )
                raise

            _logger.info(
                "VINCI_INTERCOMPANY | REVISION_SUCCESS | SO=%s",
                self.sale_order_id.name,
            )

            return {"type": "ir.actions.act_window_close"}

        if self.operation == "cancellation":
            self.sale_order_id._vinci_get_intercompany_document_summary()
            raise UserError(_(
                "Cancellation is not implemented yet. "
                "No documents have been modified."
            ))

        raise UserError(_("Invalid intercompany operation."))

    def _vinci_validate_revision(self):
        self.ensure_one()

        if self.operation != "revision":
            raise UserError(_("This validation is only for revisions."))

        sale_order = self.sale_order_id
        summary = sale_order._vinci_get_intercompany_document_summary()

        purchase_orders = summary["purchase_orders"]
        counterpart_orders = summary["counterpart_orders"]

        # First version: only undelivered and uninvoiced orders.
        all_sale_orders = sale_order | counterpart_orders

        if summary["sale_invoices"] or summary["purchase_invoices"]:
            raise UserError(_(
                "Revision is not yet supported for orders with invoices or bills."
            ))

        if any(
            picking.state == "done"
            for picking in summary["pickings"]
        ):
            raise UserError(_(
                "Revision is not yet supported for completed transfers."
            ))

        if any(
            move.state == "done"
            for move in summary["pickings"].move_ids
        ):
            raise UserError(_(
                "Revision is not yet supported for completed stock moves."
            ))

        if any(
            line.qty_delivered
            for order in all_sale_orders
            for line in order.order_line
            if not line.display_type
        ):
            raise UserError(_(
                "Revision is not yet supported for delivered quantities."
            ))

        if any(
            line.qty_received
            for po in purchase_orders
            for line in po.order_line
            if not line.display_type
        ):
            raise UserError(_(
                "Revision is not yet supported for received quantities."
            ))

        for line in self.revision_line_ids:
            if line.new_quantity < 0:
                raise UserError(_(
                    "New quantity cannot be negative."
                ))

        matches = self._vinci_match_revision_lines(summary)

        if not matches:
            raise UserError(_(
                "No valid revision lines were found."
            ))

        return {
            "summary": summary,
            "matches": matches,
        }


    def _vinci_match_revision_lines(self, summary):
        self.ensure_one()

        central_so = self.sale_order_id
        distributor_po = summary["purchase_orders"]
        distributor_so = summary["counterpart_orders"]

        if len(distributor_po) != 1 or len(distributor_so) != 1:
            raise UserError(_(
                "Exactly one distributor PO and one distributor SO are required."
            ))

        central_lines = central_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        purchase_lines = distributor_po.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        distributor_lines = distributor_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )

        if not (
            len(central_lines)
            == len(purchase_lines)
            == len(distributor_lines)
            == len(self.revision_line_ids)
        ):
            raise UserError(_(
                "The three orders must contain the same number of product lines."
            ))

        matches = []

        if len(set(self.revision_line_ids.mapped("sale_order_line_id").ids)) != len(
                    self.revision_line_ids
                ):
            raise UserError(_(
                "The same sales order line cannot be revised more than once."
            ))

        for revision_line in self.revision_line_ids:
            central_line = revision_line.sale_order_line_id

            if central_line not in central_lines:
                raise UserError(_("Invalid central sales order line."))

            po_matches = purchase_lines.filtered(
                lambda line: line.product_id == central_line.product_id
            )
            so_matches = distributor_lines.filtered(
                lambda line: line.product_id == central_line.product_id
            )

            if len(po_matches) != 1 or len(so_matches) != 1:
                raise UserError(_(
                    "Product %(product)s cannot be matched uniquely "
                    "across the intercompany documents.",
                    product=central_line.product_id.display_name,
                ))

            po_line = po_matches[0]
            distributor_line = so_matches[0]

            if not (
                central_line.product_uom == po_line.product_uom
                == distributor_line.product_uom
            ):
                raise UserError(_(
                    "Units of measure do not match for product %(product)s.",
                    product=central_line.product_id.display_name,
                ))

            if revision_line.product_id != central_line.product_id:
                raise UserError(_("Revision line product does not match."))

            matches.append({
                "central_line": central_line,
                "purchase_line": po_line,
                "distributor_line": distributor_line,
                "new_quantity": revision_line.new_quantity,
            })

        return matches


    def _vinci_lock_revision_documents(self, summary):
        self.ensure_one()

        sale_orders = self.sale_order_id | summary["counterpart_orders"]
        purchase_orders = summary["purchase_orders"]

        pickings = summary["pickings"]
        moves = pickings.move_ids

        records_to_lock = [
            ("sale_order", sorted(sale_orders.ids)),
            ("purchase_order", sorted(purchase_orders.ids)),
            ("sale_order_line", sorted(sale_orders.order_line.ids)),
            ("purchase_order_line", sorted(purchase_orders.order_line.ids)),
            ("stock_picking", sorted(pickings.ids)),
            ("stock_move", sorted(moves.ids)),
            ("stock_move_line", sorted(moves.move_line_ids.ids)),
        ]

        try:
            with self.env.cr.savepoint():
                for table_name, record_ids in records_to_lock:
                    if not record_ids:
                        continue

                    self.env.cr.execute(
                        f"SELECT id FROM {table_name} "
                        "WHERE id = ANY(%s) ORDER BY id FOR UPDATE NOWAIT",
                        (record_ids,),
                    )

                    _logger.info(
                        "VINCI_INTERCOMPANY | DOCUMENT_LOCKED "
                        "| TABLE=%s | IDS=%s",
                        table_name,
                        record_ids,
                    )

        except errors.LockNotAvailable:
            _logger.warning(
                "VINCI_INTERCOMPANY | DOCUMENT_LOCK_FAILED | SO=%s",
                self.sale_order_id.name,
            )
            raise UserError(_(
                "Another operation is currently updating these orders. "
                "Please try again shortly."
            ))

        # Refresh cached ORM values after acquiring database locks.
        self.env.invalidate_all()


    def _vinci_apply_quantity_revision(self, result):
        self.ensure_one()

        summary = result["summary"]

        # Save the document and stock move IDs before locking.
        original_document_ids = {
            "purchase_orders": set(summary["purchase_orders"].ids),
            "counterpart_orders": set(summary["counterpart_orders"].ids),
            "pickings": set(summary["pickings"].ids),
            "moves": set(summary["pickings"].move_ids.ids),
        }

        self._vinci_lock_revision_documents(summary)

        # Revalidate after acquiring the database locks.
        result = self._vinci_validate_revision()
        summary = result["summary"]
        matches = result["matches"]

        current_document_ids = {
            "purchase_orders": set(summary["purchase_orders"].ids),
            "counterpart_orders": set(summary["counterpart_orders"].ids),
            "pickings": set(summary["pickings"].ids),
            "moves": set(summary["pickings"].move_ids.ids),
        }

        if original_document_ids != current_document_ids:
            _logger.warning(
                "VINCI_INTERCOMPANY | DOCUMENT_LINKS_CHANGED | SO=%s",
                self.sale_order_id.name,
            )
            raise UserError(_(
                "Linked intercompany documents or stock moves changed "
                "during the revision. Please reopen the wizard."
            ))

        all_moves = self.env["stock.move"]
        updates = []

        for match in matches:
            central = match["central_line"]
            purchase = match["purchase_line"]
            distributor = match["distributor_line"]
            new_qty = match["new_quantity"]

            revision_line = self.revision_line_ids.filtered(
                lambda line: line.sale_order_line_id == central
            )

            rounding = central.product_uom.rounding
            old_qty = central.product_uom_qty

            if len(revision_line) != 1:
                raise UserError(_("Revision line mismatch."))

            if float_compare(
                old_qty,
                revision_line.original_quantity,
                precision_rounding=rounding,
            ) != 0:
                raise UserError(_(
                    "The order quantity changed after opening the wizard. "
                    "Please reopen the revision."
                ))

            for line_qty in (
                purchase.product_qty,
                distributor.product_uom_qty,
            ):
                if float_compare(
                    line_qty, old_qty, precision_rounding=rounding
                ) != 0:
                    raise UserError(_(
                        "Linked order quantities do not match."
                    ))

            # First implementation: positive quantities only.
            # Zero quantity will be handled by the cancellation package.
            if float_compare(
                new_qty, 0, precision_rounding=rounding
            ) <= 0:
                raise UserError(_(
                    "Quantity must be greater than zero. "
                    "Use cancellation for removing an order."
                ))

            # Reject unsupported stock configurations rather than
            # guessing which move should be updated.
            central_moves = central.move_ids.filtered(
                lambda m: m.state != "cancel"
            )
            purchase_moves = purchase.move_ids.filtered(
                lambda m: m.state != "cancel"
            )
            distributor_moves = distributor.move_ids.filtered(
                lambda m: m.state != "cancel"
            )

            move_groups = (
                central_moves,
                purchase_moves,
                distributor_moves,
            )

            if any(len(group) != 1 for group in move_groups):
                raise UserError(_(
                    "Each linked order line must have exactly one "
                    "active stock move for this revision version."
                ))

            moves = central_moves | purchase_moves | distributor_moves

            for move in moves:
                if move.state not in (
                    "confirmed", "waiting", "assigned",
                    "partially_available",
                ):
                    raise UserError(_(
                        "Unsupported stock move state: %s"
                    ) % move.state)

                if move.picked or any(move.move_line_ids.mapped("picked")):
                    raise UserError(_(
                        "Picked stock operations cannot be revised."
                    ))

                if move.move_line_ids.filtered(
                    lambda line: line.picked
                ):
                    raise UserError(_(
                        "Stock move %(move)s contains picked operations. "
                        "Revision is not allowed.",
                        move=move.id,
                    ))

                move_qty = move.product_uom._compute_quantity(
                    move.product_uom_qty, central.product_uom
                )

                if float_compare(
                    move_qty, old_qty, precision_rounding=rounding
                ) != 0:
                    raise UserError(_(
                        "Stock move quantity does not match "
                        "the original order quantity."
                    ))

            all_moves |= moves
            updates.append((match, moves))

        # Ensure there are no unexpected active moves on linked transfers.
        expected_moves = summary["pickings"].move_ids.filtered(
            lambda m: m.state != "cancel"
        )

        if set(expected_moves.ids) != set(all_moves.ids):
            _logger.error(
                "VINCI_INTERCOMPANY | STOCK_MOVE_MISMATCH "
                "| EXPECTED=%s | MATCHED=%s",
                sorted(expected_moves.ids),
                sorted(all_moves.ids),
            )
            raise UserError(_(
                "Unexpected stock moves were found on linked transfers. "
                "Revision stopped."
            ))

        original_move_ids = set(all_moves.ids)

        _logger.info(
            "VINCI_INTERCOMPANY | VALIDATION_OK | SO=%s | MOVES=%s",
            self.sale_order_id.name,
            sorted(original_move_ids),
        )

        # All checks above must succeed before any write.
        for match, moves in updates:
            central = match["central_line"]
            purchase = match["purchase_line"]
            distributor = match["distributor_line"]
            new_qty = match["new_quantity"]

            _logger.info(
                "VINCI_INTERCOMPANY | QUANTITY_CHANGE | PRODUCT=%s "
                "| OLD=%s | NEW=%s",
                central.product_id.id,
                central.product_uom_qty,
                new_qty,
            )

            # Updating demand may automatically unreserve stock.
            for move in moves:
                move_new_qty = central.product_uom._compute_quantity(
                    new_qty, move.product_uom
                )

                _logger.info(
                    "VINCI_INTERCOMPANY | STOCK_MOVE_UPDATE "
                    "| MOVE=%s | OLD=%s | NEW=%s",
                    move.id,
                    move.product_uom_qty,
                    move_new_qty,
                )

                move.write({"product_uom_qty": move_new_qty})

            central.with_context(
                skip_procurement=True
            ).write({"product_uom_qty": new_qty})

            distributor.with_context(
                skip_procurement=True
            ).write({"product_uom_qty": new_qty})

            purchase.with_context(
                vinci_skip_purchase_picking_update=True
            ).write({"product_qty": new_qty})

        # Check that the operation did not create additional moves.
        final_moves = summary["pickings"].move_ids.filtered(
            lambda m: m.state != "cancel"
        )

        if set(final_moves.ids) != original_move_ids:
            raise UserError(_(
                "Unexpected stock moves were created during revision."
            ))


        # Reassign only moves that lost their reservation and are
        # eligible for standard Odoo stock assignment.
        moves_to_assign = all_moves.filtered(
            lambda move:
                move.state in ("confirmed", "partially_available")
                and not move.picked
                and not move.move_orig_ids
        )

        if moves_to_assign:
            _logger.info(
                "VINCI_INTERCOMPANY | STOCK_REASSIGN_START | MOVES=%s",
                moves_to_assign.ids,
            )
            moves_to_assign._action_assign()

        # Verify all order lines and stock moves after the update.
        for match, moves in updates:
            central = match["central_line"]
            purchase = match["purchase_line"]
            distributor = match["distributor_line"]
            new_qty = match["new_quantity"]
            rounding = central.product_uom.rounding

            quantities = (
                central.product_uom_qty,
                purchase.product_qty,
                distributor.product_uom_qty,
            )

            if any(
                float_compare(qty, new_qty, precision_rounding=rounding) != 0
                for qty in quantities
            ):
                raise UserError(_(
                    "Final intercompany order quantities do not match."
                ))

            for move in moves:
                expected_qty = central.product_uom._compute_quantity(
                    new_qty, move.product_uom
                )
                if float_compare(
                    move.product_uom_qty,
                    expected_qty,
                    precision_rounding=move.product_uom.rounding,
                ) != 0:
                    raise UserError(_(
                        "Final stock move quantity does not match."
                    ))


                # Reserved quantity must never exceed the new demand.
                if float_compare(
                    move.quantity,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                ) > 0:
                    _logger.error(
                        "VINCI_INTERCOMPANY | EXCESS_RESERVATION "
                        "| MOVE=%s | DEMAND=%s | RESERVED=%s",
                        move.id,
                        move.product_uom_qty,
                        move.quantity,
                    )
                    raise UserError(_(
                        "Stock move %(move)s has a quantity greater "
                        "than its new demand. Revision stopped.",
                        move=move.id,
                    ))

                if move.state == "assigned" and float_compare(
                    move.quantity,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                ) != 0:
                    raise UserError(_(
                        "Assigned stock move %(move)s does not have "
                        "the expected reserved quantity.",
                        move=move.id,
                    ))


                _logger.info(
                    "VINCI_INTERCOMPANY | FINAL_STOCK_STATE "
                    "| MOVE=%s | STATE=%s | DEMAND=%s | RESERVED=%s",
                    move.id,
                    move.state,
                    move.product_uom_qty,
                    move.quantity,
                )


        _logger.info(
            "VINCI_INTERCOMPANY | QUANTITY_UPDATE_COMPLETED | SO=%s",
            self.sale_order_id.name,
        )
