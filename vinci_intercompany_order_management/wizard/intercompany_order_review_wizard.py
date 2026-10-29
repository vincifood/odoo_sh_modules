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

    # Immutable snapshot of original order lines, so deleting a row in the
    # transient wizard cannot silently skip an existing sales order line.
    original_line_snapshot = fields.Json(readonly=True, default=dict)

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
            summary = self.sale_order_id._vinci_get_intercompany_document_summary()
            if any(p.state == "done" for p in summary["pickings"]):
                return self._vinci_prepare_delivery_returns()
            return self._vinci_cancel_undelivered_intercompany()

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

        # Package 2: Validate whole-box quantities before matching order lines.
        self.revision_line_ids._vinci_validate_box_quantity()

        new_lines = self.revision_line_ids.filtered(
            lambda line: not line.sale_order_line_id
        )

        matches = self._vinci_match_revision_lines(summary)

        if not matches and not new_lines:
            raise UserError(_(
                "No valid revision lines were found."
            ))

        return {
            "summary": summary,
            "matches": matches,
            "new_lines": new_lines,
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

        central_company = central_so.company_id
        distributor_company = distributor_po.company_id

        if distributor_so.company_id != distributor_company:
            raise UserError(_(
                "Distributor PO and SO belong to different companies."
            ))

        if (
            central_company not in self.env.user.company_ids
            or distributor_company not in self.env.user.company_ids
        ):
            raise UserError(_(
                "You must have access to both the central "
                "and distributor companies."
            ))

        central_lines = central_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id and line.product_uom_qty > 0
        )
        purchase_lines = distributor_po.order_line.filtered(
            lambda line: not line.display_type and line.product_id and line.product_qty > 0
        )
        distributor_lines = distributor_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id and line.product_uom_qty > 0
        )


        existing_revision_lines = self.revision_line_ids.filtered(
            lambda line: bool(line.sale_order_line_id)
        )
        new_revision_lines = self.revision_line_ids.filtered(
            lambda line: not line.sale_order_line_id
        )


        if not (
            len(central_lines) == len(purchase_lines) == len(distributor_lines)
        ):
            raise UserError(_(
                "The three orders must contain the same number of product lines."
            ))

        snapshot = self.original_line_snapshot or {}
        if set(map(str, central_lines.ids)) != set(snapshot):
            raise UserError(_(
                "Original sales order lines have changed. Please reopen the wizard."
            ))

        matches = []

        if len(set(existing_revision_lines.mapped("sale_order_line_id").ids)) != len(
            existing_revision_lines
        ):
            raise UserError(_(
                "The same sales order line cannot be revised more than once."
            ))

        for revision_line in existing_revision_lines:
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

        # A missing original wizard row means the user deleted that row.
        # Convert it to an explicit zero-quantity revision for all 3 orders.
        visible_ids = set(existing_revision_lines.mapped("sale_order_line_id").ids)
        for central_line in central_lines.filtered(lambda rec: rec.id not in visible_ids):
            po_matches = purchase_lines.filtered(
                lambda rec: rec.product_id == central_line.product_id
            )
            so_matches = distributor_lines.filtered(
                lambda rec: rec.product_id == central_line.product_id
            )
            if len(po_matches) != 1 or len(so_matches) != 1:
                raise UserError(_(
                    "A removed product cannot be matched uniquely across linked orders."
                ))
            matches.append({
                "central_line": central_line,
                "purchase_line": po_matches[0],
                "distributor_line": so_matches[0],
                "new_quantity": 0.0,
                "original_quantity": snapshot[str(central_line.id)],
                "deleted_in_wizard": True,
            })

        return matches


    def _vinci_lock_revision_documents(self, summary):
        self.ensure_one()

        sale_orders = self.sale_order_id | summary["counterpart_orders"]
        purchase_orders = summary["purchase_orders"]

        pickings = summary["pickings"]
        picking_moves = pickings.move_ids

        # Include directly connected stock moves in database locking.
        # This protects against concurrent updates to linked moves.
        connected_moves = (
            picking_moves.move_orig_ids
            | picking_moves.move_dest_ids
        )

        moves = picking_moves | connected_moves

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
        new_lines = result["new_lines"]

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

        # Package 2: Precheck new products after document locking.
        # No order lines or stock moves are created here.
        if new_lines:
            self._vinci_add_revision_products(
                result,
                validate_only=True,
            )

        all_moves = self.env["stock.move"]
        updates = []
        canceled_move_ids = set()

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

            if not match.get("deleted_in_wizard") and len(revision_line) != 1:
                raise UserError(_("Revision line mismatch."))

            original_qty = (self.original_line_snapshot or {}).get(str(central.id))
            if original_qty is None or float_compare(
                old_qty,
                original_qty,
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

            # Negative quantities are forbidden.
            # Zero means removing the product from all linked orders.
            if float_compare(
                new_qty, 0, precision_rounding=rounding
            ) < 0:
                raise UserError(_(
                    "Quantity cannot be negative."
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

            # Package 2: Prevent cancellation from affecting
            # stock moves outside the three linked order lines.
            if float_compare(
                new_qty, 0, precision_rounding=rounding
            ) == 0:
                linked_moves = (
                    moves.move_orig_ids | moves.move_dest_ids
                ).filtered(lambda move: move.state != "cancel")

                external_moves = linked_moves - moves

                if external_moves:
                    raise UserError(_(
                        "Product %(product)s cannot be removed because "
                        "its stock moves are linked to external moves: %(moves)s.",
                        product=central.product_id.display_name,
                        moves=", ".join(map(str, external_moves.ids)),
                    ))

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

        # Snapshot the cancellation scope before modifying any moves.
        original_move_states = {
            move.id: move.state
            for move in all_moves
        }

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

            # Package 2: Remove an existing product.
            if float_compare(
                new_qty,
                0,
                precision_rounding=central.product_uom.rounding,
            ) == 0:

                _logger.info(
                    "VINCI_INTERCOMPANY | PRODUCT_REMOVAL_START "
                    "| PRODUCT=%s | MOVES=%s",
                    central.product_id.id,
                    moves.ids,
                )

                # Cancel only the validated moves.
                # External active dependencies were rejected above.
                moves._action_cancel()

                if any(move.state != "cancel" for move in moves):
                    raise UserError(_(
                        "Not all stock moves were cancelled for product %s."
                    ) % central.product_id.display_name)

                # Keep the order lines for traceability.
                central.with_context(
                    skip_procurement=True
                ).write({"product_uom_qty": 0})

                distributor.with_context(
                    skip_procurement=True
                ).write({"product_uom_qty": 0})

                purchase.with_context(
                    vinci_skip_purchase_picking_update=True
                ).write({"product_qty": 0})

                canceled_move_ids.update(moves.ids)

                _logger.info(
                    "VINCI_INTERCOMPANY | PRODUCT_REMOVAL_COMPLETED "
                    "| PRODUCT=%s",
                    central.product_id.id,
                )

                continue

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

        expected_remaining_move_ids = (
            original_move_ids - canceled_move_ids
        )

        # Every explicitly removed move must now be cancelled.
        for move in all_moves.filtered(
            lambda m: m.id in canceled_move_ids
        ):
            if move.state != "cancel":
                raise UserError(_(
                    "Stock move %s was not cancelled correctly."
                ) % move.id)

        # Moves belonging to products that were not removed
        # must not be cancelled as a side effect.
        unexpectedly_cancelled = all_moves.filtered(
            lambda m:
                m.id not in canceled_move_ids
                and original_move_states.get(m.id) != "cancel"
                and m.state == "cancel"
        )

        if unexpectedly_cancelled:
            raise UserError(_(
                "Stock cancellation affected unrelated order lines. "
                "Affected stock moves: %s"
            ) % ", ".join(map(str, unexpectedly_cancelled.ids)))

        if set(final_moves.ids) != expected_remaining_move_ids:
            raise UserError(_(
                "Unexpected active stock moves were found after revision. "
                "A stock move may have been created or cancelled unexpectedly."
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

            # Verify removed products separately.
            if float_compare(
                new_qty, 0, precision_rounding=rounding
            ) == 0:

                if any(move.state != "cancel" for move in moves):
                    raise UserError(_(
                        "Removed product still has active stock moves."
                    ))

                if any(
                    float_compare(
                        qty, 0, precision_rounding=rounding
                    ) != 0
                    for qty in (
                        central.product_uom_qty,
                        purchase.product_qty,
                        distributor.product_uom_qty,
                    )
                ):
                    raise UserError(_(
                        "Removed product still has a non-zero "
                        "quantity in a linked order."
                    ))

                continue

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

        # Package 2: Add new products only after all existing
        # quantity and stock validations have succeeded.
        if new_lines:
            self._vinci_add_revision_products(result)

    def _vinci_add_revision_products(self, result, validate_only=False):
        """Validate new products before adding intercompany order lines."""
        self.ensure_one()

        summary = result["summary"]
        new_lines = result.get("new_lines", self.env[
            "vinci.intercompany.revision.line"
        ])

        if not new_lines:
            return

        # Package 2: Only confirmed intercompany orders are supported.
        central_so = self.sale_order_id
        distributor_po = summary["purchase_orders"]
        distributor_so = summary["counterpart_orders"]

        if (
            central_so.state != "sale"
            or distributor_po.state != "purchase"
            or distributor_so.state != "sale"
        ):
            raise UserError(_(
                "All three intercompany orders must be confirmed "
                "before adding new products."
            ))

        if len(distributor_po) != 1 or len(distributor_so) != 1:
            raise UserError(_(
                "Exactly one linked distributor PO and SO are required."
            ))

        central_company = central_so.company_id
        distributor_company = distributor_po.company_id

        if distributor_so.company_id != distributor_company:
            raise UserError(_(
                "Distributor PO and SO belong to different companies."
            ))

        existing_products = (
            central_so.order_line.mapped("product_id")
            | distributor_po.order_line.mapped("product_id")
            | distributor_so.order_line.mapped("product_id")
        )

        seen_products = set()

        for line in new_lines:
            product = line.product_id

            if not product.active:
                raise UserError(_(
                    "Product %s is archived and cannot be added."
                ) % product.display_name)

            if product.company_id:
                raise UserError(_(
                    "Product %s is company-specific. "
                    "Only shared products can be added until "
                    "cross-company product mapping is supported."
                ) % product.display_name)

            if product.type != "consu":
                raise UserError(_(
                    "Product %s must be a stockable or consumable "
                    "product for this intercompany revision."
                ) % product.display_name)

            if product in existing_products:
                raise UserError(_(
                    "Product %s already exists in a linked order. "
                    "Please revise its existing line instead."
                ) % product.display_name)

            if product.id in seen_products:
                raise UserError(_(
                    "Product %s was added more than once."
                ) % product.display_name)

            seen_products.add(product.id)

        _logger.info(
            "VINCI_INTERCOMPANY | NEW_PRODUCTS_VALIDATED "
            "| SO=%s | PRODUCTS=%s",
            central_so.name,
            sorted(seen_products),
        )

        # Reuse the central order's actual stock route, not Dropshipping.
        # A False route is valid: the warehouse's normal delivery rule applies.
        central_routes = central_so.order_line.filtered(
            lambda line: not line.display_type and line.product_id
            and line.product_uom_qty > 0
        ).mapped("route_id")
        if len(central_routes) > 1:
            raise UserError(_(
                "Central SO lines have different routes. Cannot choose one safely."
            ))
        central_route = central_routes[:1]

        distributor_routes = distributor_so.order_line.filtered(
            lambda line:
                not line.display_type
                and line.product_id.type == "consu"
                and bool(line.route_id)
        ).mapped("route_id")

        if len(distributor_routes) != 1:
            raise UserError(_(
                "Cannot determine a unique Cold Sale route from "
                "the existing distributor sales order."
            ))

        distributor_cold_route = distributor_routes

        prepared_lines = []

        for line in new_lines:
            product = line.product_id
            packaging = line.product_packaging_id
            quantity = line.new_quantity

            box_count = quantity / packaging.qty

            distributor_so_vals = {
                "order_id": distributor_so.id,
                "product_id": product.id,
                "product_uom_qty": quantity,
                "product_uom": line.product_uom_id.id,
                "product_packaging_id": packaging.id,
                "product_packaging_qty": box_count,
                "route_id": distributor_cold_route.id,
            }

            distributor_po_vals = {
                "order_id": distributor_po.id,
                "product_id": product.id,
                "product_qty": quantity,
                "product_uom": line.product_uom_id.id,
                "product_packaging_id": packaging.id,
                "product_packaging_qty": box_count,
                "name": product.display_name,
                "date_planned": fields.Datetime.now(),
            }

            central_so_vals = {
                "order_id": central_so.id,
                "product_id": product.id,
                "product_uom_qty": quantity,
                "product_uom": line.product_uom_id.id,
                "product_packaging_id": packaging.id,
                "product_packaging_qty": box_count,
                "route_id": central_route.id if central_route else False,
            }

            prepared_lines.append({
                "product": product,
                "quantity": quantity,
                "packaging": packaging,
                "distributor_so_vals": distributor_so_vals,
                "distributor_po_vals": distributor_po_vals,
                "central_so_vals": central_so_vals,
            })

        _logger.info(
            "VINCI_INTERCOMPANY | NEW_PRODUCT_LINES_PREPARED "
            "| SO=%s | COUNT=%s",
            central_so.name,
            len(prepared_lines),
        )

        # Validate new products without creating order lines.
        if validate_only:
            _logger.info(
                "VINCI_INTERCOMPANY | NEW_PRODUCTS_PRECHECK_OK "
                "| SO=%s | COUNT=%s",
                central_so.name,
                len(prepared_lines),
            )
            return

        # Prepare company-specific environments.
        CentralSaleLine = self.env["sale.order.line"].with_company(
            central_company
        )

        DistributorSaleLine = self.env["sale.order.line"].with_company(
            distributor_company
        )

        DistributorPurchaseLine = self.env["purchase.order.line"].with_company(
            distributor_company
        )

        _logger.info(
            "VINCI_INTERCOMPANY | COMPANY_CONTEXT_READY "
            "| CENTRAL=%s | DISTRIBUTOR=%s",
            central_company.name,
            distributor_company.name,
        )

        created_lines = []

        for prepared in prepared_lines:
            # Create the distributor's customer sales line.
            distributor_sale_line = DistributorSaleLine.create(
                prepared["distributor_so_vals"]
            )

            _logger.info(
                "VINCI_INTERCOMPANY | DISTRIBUTOR_PRICE_CHECK "
                "| PRODUCT=%s | PRICE=%s | DISCOUNT=%s "
                "| PRICELIST=%s",
                distributor_sale_line.product_id.id,
                distributor_sale_line.price_unit,
                distributor_sale_line.discount,
                distributor_so.pricelist_id.id,
            )

            if not distributor_sale_line.currency_id:
                raise UserError(_(
                    "No currency is configured for the distributor "
                    "sales order."
                ))

            if not distributor_so.pricelist_id:
                raise UserError(_(
                    "No pricelist is configured for the distributor "
                    "sales order."
                ))

            if not 0 <= distributor_sale_line.discount <= 100:
                raise UserError(_(
                    "Invalid discount percentage for product %s."
                ) % distributor_sale_line.product_id.display_name)

            # Create the distributor's purchase line.
            distributor_purchase_line = DistributorPurchaseLine.create(
                prepared["distributor_po_vals"]
            )

            _logger.info(
                "VINCI_INTERCOMPANY | PRICE_COMPARISON "
                "| PRODUCT=%s | CUSTOMER_PRICE=%s "
                "| CUSTOMER_DISCOUNT=%s | PURCHASE_PRICE=%s "
                "| PURCHASE_DISCOUNT=%s",
                prepared["product"].id,
                distributor_sale_line.price_unit,
                distributor_sale_line.discount,
                distributor_purchase_line.price_unit,
                distributor_purchase_line.discount,
            )

            # Prepare Central SO pricing from the linked Distribution PO.
            central_vals = distributor_po._prepare_sale_order_line_data(
                distributor_purchase_line,
                central_company,
            )

            # Preserve the validated product, quantity, packaging and route.
            central_vals.update(prepared["central_so_vals"])

            # Create Central SO line with intercompany pricing.
            central_sale_line = CentralSaleLine.create(central_vals)

            _logger.info(
                "VINCI_INTERCOMPANY | INTERCOMPANY_PRICE_CHECK "
                "| PRODUCT=%s | PO_PRICE=%s | PO_DISCOUNT=%s "
                "| CENTRAL_PRICE=%s | CENTRAL_DISCOUNT=%s",
                prepared["product"].id,
                distributor_purchase_line.price_unit,
                distributor_purchase_line.discount,
                central_sale_line.price_unit,
                central_sale_line.discount,
            )

            # Prices and discounts are company/document-specific. They can
            # legally differ, including promotional discounts on Central SO.
            # Do not enforce PO net price == Central SO net price.
            if not central_sale_line.currency_id or not distributor_purchase_line.currency_id:
                raise UserError(_(
                    "A linked order line has no currency configured."
                ))

            created_lines.append({
                "product": prepared["product"],
                "central": central_sale_line,
                "purchase": distributor_purchase_line,
                "distributor": distributor_sale_line,
            })

            _logger.info(
                "VINCI_INTERCOMPANY | NEW_PRODUCT_CREATED "
                "| PRODUCT=%s | CENTRAL_LINE=%s "
                "| PURCHASE_LINE=%s | DISTRIBUTOR_LINE=%s",
                prepared["product"].id,
                central_sale_line.id,
                distributor_purchase_line.id,
                distributor_sale_line.id,
            )

        # New lines on already-confirmed orders may not launch procurement
        # automatically. Ask Odoo's standard engines to create the stock moves.
        # They must stay within the three original picking groups; otherwise
        # the strict checks below fail and the entire RPC transaction rolls back.
        for created in created_lines:
            created["central"].with_company(central_company)._action_launch_stock_rule()
            created["distributor"].with_company(distributor_company)._action_launch_stock_rule()
        distributor_po.with_company(distributor_company)._create_picking()

        for created in created_lines:
            product = created["product"]

            central_line = created["central"]
            purchase_line = created["purchase"]
            distributor_line = created["distributor"]

            move_groups = {
                "Central SO": central_line.move_ids.filtered(
                    lambda m: m.state != "cancel"
                ),
                "Distribution PO": purchase_line.move_ids.filtered(
                    lambda m: m.state != "cancel"
                ),
                "Distribution SO": distributor_line.move_ids.filtered(
                    lambda m: m.state != "cancel"
                ),
            }

            for document_name, moves in move_groups.items():
                if len(moves) != 1:
                    raise UserError(_(
                        "New product %(product)s must have exactly "
                        "one active stock move in %(document)s. "
                        "Found: %(count)s.",
                        product=product.display_name,
                        document=document_name,
                        count=len(moves),
                    ))

                if not moves.picking_id:
                    raise UserError(_(
                        "New product %(product)s has no linked "
                        "stock picking in %(document)s.",
                        product=product.display_name,
                        document=document_name,
                    ))

                expected_pickings = (
                    central_so.picking_ids
                    if document_name == "Central SO"
                    else distributor_po.picking_ids
                    if document_name == "Distribution PO"
                    else distributor_so.picking_ids
                )

                if moves.picking_id not in expected_pickings:
                    raise UserError(_(
                        "New product %(product)s has an unexpected "
                        "picking in %(document)s.",
                        product=product.display_name,
                        document=document_name,
                    ))

                expected_company = (
                    central_so.company_id
                    if document_name == "Central SO"
                    else distributor_po.company_id
                )

                if moves.company_id != expected_company:
                    raise UserError(_(
                        "Stock move company mismatch for %(product)s "
                        "in %(document)s.",
                        product=product.display_name,
                        document=document_name,
                    ))

                if moves.picking_id.company_id != expected_company:
                    raise UserError(_(
                        "Stock picking company mismatch for %(product)s "
                        "in %(document)s.",
                        product=product.display_name,
                        document=document_name,
                    ))

                expected_qty = created["central"].product_uom._compute_quantity(
                    created["central"].product_uom_qty,
                    moves.product_uom,
                )

                if float_compare(
                    moves.product_uom_qty,
                    expected_qty,
                    precision_rounding=moves.product_uom.rounding,
                ) != 0:
                    raise UserError(_(
                        "Stock quantity mismatch for %(product)s "
                        "in %(document)s.",
                        product=product.display_name,
                        document=document_name,
                    ))
