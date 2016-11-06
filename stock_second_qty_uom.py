# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from openerp import fields, models, api

from datetime import date, datetime
from dateutil import relativedelta
import json
import time
import sets
from openerp.tools.float_utils import float_compare, float_round
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from openerp import SUPERUSER_ID, api, models
import openerp.addons.decimal_precision as dp
from openerp.exceptions import UserError
class stock_pack_operation(models.Model):
    _inherit = 'stock.pack.operation'
    
    second_qty = fields.Float("Second_Qty", default=False)
    second_product_uom_id = fields.Many2one('product.uom', string = 'Second Unit of Measure')
    second_product_qty = fields.Float('Second To Do', digits_compute=dp.get_precision('Second Product Unit of Measure'), required=False, default=0.0)
    second_qty_done = fields.Float('Second Done', digits_compute=dp.get_precision('Second Product Unit of Measure'), default = 0.0)
    
    def _get_remaining_prod_quantities(self, cr, uid, operation, context=None):
        '''Get the remaining quantities per product on an operation with a package. This function returns a dictionary'''
        #if the operation doesn't concern a package, it's not relevant to call this function
        if not operation.package_id or operation.product_id:
            return {operation.product_id: operation.remaining_qty}
        #get the total of products the package contains
        res = self.pool.get('stock.quant.package')._get_all_products_quantities(cr, uid, operation.package_id.id, context=context)
        #reduce by the quantities linked to a move
        for record in operation.linked_move_operation_ids:
            if record.move_id.product_id.id not in res:
                res[record.move_id.product_id] = 0
            res[record.move_id.product_id] -= record.qty
        return res

    def _get_remaining_qty(self, cr, uid, ids, name, args, context=None):
        uom_obj = self.pool.get('product.uom')
        res = {}
        for ops in self.browse(cr, uid, ids, context=context):
            res[ops.id] = 0
            if ops.package_id and not ops.product_id:
                #dont try to compute the remaining quantity for packages because it's not relevant (a package could include different products).
                #should use _get_remaining_prod_quantities instead
                continue
            else:
                qty = ops.product_qty
                if ops.product_uom_id:
                    qty = uom_obj._compute_qty_obj(cr, uid, ops.product_uom_id, ops.product_qty, ops.product_id.uom_id, context=context)
                for record in ops.linked_move_operation_ids:
                    qty -= record.qty
                res[ops.id] = float_round(qty, precision_rounding=ops.product_id.uom_id.rounding)
        return res

    def product_id_change(self, cr, uid, ids, product_id, product_uom_id, product_qty, context=None):
        res = self.on_change_tests(cr, uid, ids, product_id, product_uom_id, product_qty, context=context)
        uom_obj = self.pool['product.uom']
        product = self.pool.get('product.product').browse(cr, uid, product_id, context=context)
        if product_id and not product_uom_id or uom_obj.browse(cr, uid, product_uom_id, context=context).category_id.id != product.uom_id.category_id.id:
            res['value']['product_uom_id'] = product.uom_id.id
        if product:
            res['value']['lots_visible'] = (product.tracking != 'none')
            res['domain'] = {'product_uom_id': [('category_id','=',product.uom_id.category_id.id)]}
        else:
            res['domain'] = {'product_uom_id': []}
        return res

    def on_change_tests(self, cr, uid, ids, product_id, product_uom_id, product_qty, context=None):
        res = {'value': {}}
        uom_obj = self.pool.get('product.uom')
        if product_id:
            product = self.pool.get('product.product').browse(cr, uid, product_id, context=context)
            product_uom_id = product_uom_id or product.uom_id.id
            selected_uom = uom_obj.browse(cr, uid, product_uom_id, context=context)
            if selected_uom.category_id.id != product.uom_id.category_id.id:
                res['warning'] = {
                    'title': _('Warning: wrong UoM!'),
                    'message': _('The selected UoM for product %s is not compatible with the UoM set on the product form. \nPlease choose an UoM within the same UoM category.') % (product.name)
                }
            if product_qty and 'warning' not in res:
                rounded_qty = uom_obj._compute_qty(cr, uid, product_uom_id, product_qty, product_uom_id, round=True)
                if rounded_qty != product_qty:
                    res['warning'] = {
                        'title': _('Warning: wrong quantity!'),
                        'message': _('The chosen quantity for product %s is not compatible with the UoM rounding. It will be automatically converted at confirmation') % (product.name)
                    }
        return res

    def _compute_location_description(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        for op in self.browse(cr, uid, ids, context=context):
            from_name = op.location_id.name
            to_name = op.location_dest_id.name
            if op.package_id and op.product_id:
                from_name += " : " + op.package_id.name
            if op.result_package_id:
                to_name += " : " + op.result_package_id.name
            res[op.id] = {'from_loc': from_name,
                          'to_loc': to_name}
        return res

    def show_details(self, cr, uid, ids, context=None):
        data_obj = self.pool['ir.model.data']
        view = data_obj.xmlid_to_res_id(cr, uid, 'stock.view_pack_operation_details_form_save')
        pack = self.browse(cr, uid, ids[0], context=context)
        return {
             'name': _('Operation Details'),
             'type': 'ir.actions.act_window',
             'view_type': 'form',
             'view_mode': 'form',
             'res_model': 'stock.pack.operation',
             'views': [(view, 'form')],
             'view_id': view,
             'target': 'new',
             'res_id': pack.id,
             'context': context,
        }


    #second_remaining_qty = Fields.function(_get_remaining_qty, type='float', digits = 0, string="Second Remaining Qty", help="Remaining quantity in default UoM according to moves matched with this operation. ")


class stock_quant(models.Model):
    """
    Quants are the smallest unit of stock physical instances
    """
    _inherit = 'stock.quant'

    def _calc_inventory_value(self, cr, uid, ids, name, attr, context=None):
        context = dict(context or {})
        res = {}
        uid_company_id = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id.id
        for quant in self.browse(cr, uid, ids, context=context):
            context.pop('force_company', None)
            if quant.company_id.id != uid_company_id:
                #if the company of the quant is different than the current user company, force the company in the context
                #then re-do a browse to read the property fields for the good company.
                context['force_company'] = quant.company_id.id
                quant = self.browse(cr, uid, quant.id, context=context)
            res[quant.id] = self._get_inventory_value(cr, uid, quant, context=context)
        return res

    def _get_inventory_value(self, cr, uid, quant, context=None):
        return quant.product_id.standard_price * quant.qty

    second_qty = fields.Float('Quantity', required=True, help="Quantity of products in this quant, in the default unit of measure of the product", readonly=True, select=True)
    #second_product_uom_id = fields.Related('product_id', 'uom_id', type='many2one', relation="product.uom", string='Second Unit of Measure', readonly=True)
    #second_inventory_value = fields.function(_calc_inventory_value, string="Inventory Value", type='float', readonly=True)

    def read_group(self, cr, uid, domain, fields, groupby, offset=0, limit=None, context=None, orderby=False, lazy=True):
        ''' Overwrite the read_group in order to sum the function field 'inventory_value' in group by'''
        res = super(stock_quant, self).read_group(cr, uid, domain, fields, groupby, offset=offset, limit=limit, context=context, orderby=orderby, lazy=lazy)
        if 'inventory_value' in fields:
            for line in res:
                if '__domain' in line:
                    lines = self.search(cr, uid, line['__domain'], context=context)
                    inv_value = 0.0
                    for line2 in self.browse(cr, uid, lines, context=context):
                        inv_value += line2.inventory_value
                    line['inventory_value'] = inv_value
        return res

    def quants_reserve(self, cr, uid, quants, move, link=False, context=None):
        '''This function reserves quants for the given move (and optionally given link). If the total of quantity reserved is enough, the move's state
        is also set to 'assigned'

        :param quants: list of tuple(quant browse record or None, qty to reserve). If None is given as first tuple element, the item will be ignored. Negative quants should not be received as argument
        :param move: browse record
        :param link: browse record (stock.move.operation.link)
        '''
        toreserve = []
        reserved_availability = move.reserved_availability
        #split quants if needed
        for quant, qty in quants:
            if qty <= 0.0 or (quant and quant.qty <= 0.0):
                raise UserError(_('You can not reserve a negative quantity or a negative quant.'))
            if not quant:
                continue
            self._quant_split(cr, uid, quant, qty, context=context)
            toreserve.append(quant.id)
            reserved_availability += quant.qty
        #reserve quants
        if toreserve:
            self.write(cr, SUPERUSER_ID, toreserve, {'reservation_id': move.id}, context=context)
        #check if move'state needs to be set as 'assigned'
        rounding = move.product_id.uom_id.rounding
        if float_compare(reserved_availability, move.product_qty, precision_rounding=rounding) == 0 and move.state in ('confirmed', 'waiting')  :
            self.pool.get('stock.move').write(cr, uid, [move.id], {'state': 'assigned'}, context=context)
        elif float_compare(reserved_availability, 0, precision_rounding=rounding) > 0 and not move.partially_available:
            self.pool.get('stock.move').write(cr, uid, [move.id], {'partially_available': True}, context=context)

    def quants_move(self, cr, uid, quants, move, location_to, location_from=False, lot_id=False, owner_id=False, src_package_id=False, dest_package_id=False, entire_pack=False, context=None):
        """Moves all given stock.quant in the given destination location.  Unreserve from current move.
        :param quants: list of tuple(browse record(stock.quant) or None, quantity to move)
        :param move: browse record (stock.move)
        :param location_to: browse record (stock.location) depicting where the quants have to be moved
        :param location_from: optional browse record (stock.location) explaining where the quant has to be taken (may differ from the move source location in case a removal strategy applied). This parameter is only used to pass to _quant_create if a negative quant must be created
        :param lot_id: ID of the lot that must be set on the quants to move
        :param owner_id: ID of the partner that must own the quants to move
        :param src_package_id: ID of the package that contains the quants to move
        :param dest_package_id: ID of the package that must be set on the moved quant
        """
        quants_reconcile = []
        to_move_quants = []
        self._check_location(cr, uid, location_to, context=context)
        check_lot = False
        for quant, qty in quants:
            if not quant:
                #If quant is None, we will create a quant to move (and potentially a negative counterpart too)
                quant = self._quant_create(cr, uid, qty, move, lot_id=lot_id, owner_id=owner_id, src_package_id=src_package_id, dest_package_id=dest_package_id, force_location_from=location_from, force_location_to=location_to, context=context)
                check_lot = True
            else:
                self._quant_split(cr, uid, quant, qty, context=context)
                to_move_quants.append(quant)
            quants_reconcile.append(quant)
        if to_move_quants:
            to_recompute_move_ids = [x.reservation_id.id for x in to_move_quants if x.reservation_id and x.reservation_id.id != move.id]
            self.move_quants_write(cr, uid, to_move_quants, move, location_to, dest_package_id, lot_id=lot_id, entire_pack=entire_pack, context=context)
            self.pool.get('stock.move').recalculate_move_state(cr, uid, to_recompute_move_ids, context=context)
        if location_to.usage == 'internal':
            # Do manual search for quant to avoid full table scan (order by id)
            cr.execute("""
                SELECT 0 FROM stock_quant, stock_location WHERE product_id = %s AND stock_location.id = stock_quant.location_id AND
                ((stock_location.parent_left >= %s AND stock_location.parent_left < %s) OR stock_location.id = %s) AND qty < 0.0 LIMIT 1
            """, (move.product_id.id, location_to.parent_left, location_to.parent_right, location_to.id))
            if cr.fetchone():
                for quant in quants_reconcile:
                    self._quant_reconcile_negative(cr, uid, quant, move, context=context)

        # In case of serial tracking, check if the product does not exist somewhere internally already
        # Checking that a positive quant already exists in an internal location is too restrictive.
        # Indeed, if a warehouse is configured with several steps (e.g. "Pick + Pack + Ship") and
        # one step is forced (creates a quant of qty = -1.0), it is not possible afterwards to
        # correct the inventory unless the product leaves the stock.
        picking_type = move.picking_id and move.picking_id.picking_type_id or False
        if check_lot and lot_id and move.product_id.tracking == 'serial' and (not picking_type or (picking_type.use_create_lots or picking_type.use_existing_lots)):
            other_quants = self.search(cr, uid, [('product_id', '=', move.product_id.id), ('lot_id', '=', lot_id),
                                                 ('location_id.usage', '=', 'internal')], context=context)

            if other_quants:
                # We raise an error if:
                # - the total quantity is strictly larger than 1.0
                # - there are more than one negative quant, to avoid situations where the user would
                #   force the quantity at several steps of the process
                other_quants = self.browse(cr, uid, other_quants, context=context)
                if sum(other_quants.mapped('qty')) > 1.0 or len([q for q in other_quants.mapped('qty') if q < 0]) > 1:
                    lot_name = self.pool['stock.production.lot'].browse(cr, uid, lot_id, context=context).name
                    raise UserError(_('The serial number %s is already in stock.') % lot_name + _("Otherwise make sure the right stock/owner is set."))

    def quants_get_preferred_domain(self, cr, uid, qty, move, ops=False, lot_id=False, domain=None, preferred_domain_list=[], context=None):
        ''' This function tries to find quants for the given domain and move/ops, by trying to first limit
            the choice on the quants that match the first item of preferred_domain_list as well. But if the qty requested is not reached
            it tries to find the remaining quantity by looping on the preferred_domain_list (tries with the second item and so on).
            Make sure the quants aren't found twice => all the domains of preferred_domain_list should be orthogonal
        '''
        context = context or {}
        domain = domain or [('qty', '>', 0.0)]
        domain = list(domain)
        quants = [(None, qty)]
        if ops:
            restrict_lot_id = lot_id
            location = ops.location_id
            if ops.owner_id:
                domain += [('owner_id', '=', ops.owner_id.id)]
            if ops.package_id and not ops.product_id:
                domain += [('package_id', 'child_of', ops.package_id.id)]
            elif ops.package_id and ops.product_id:
                domain += [('package_id', '=', ops.package_id.id)]
            else:
                domain += [('package_id', '=', False)]
            domain += [('location_id', '=', ops.location_id.id)]
        else:
            restrict_lot_id = move.restrict_lot_id.id
            location = move.location_id
            if move.restrict_partner_id:
                domain += [('owner_id', '=', move.restrict_partner_id.id)]
            domain += [('location_id', 'child_of', move.location_id.id)]
        if context.get('force_company'): 
            domain += [('company_id', '=', context.get('force_company'))]
        else:
            domain += [('company_id', '=', move.company_id.id)]
        removal_strategy = self.pool.get('stock.location').get_removal_strategy(cr, uid, qty, move, ops=ops, context=context)
        product = move.product_id
        domain += [('product_id', '=', move.product_id.id)]

        #don't look for quants in location that are of type production, supplier or inventory.
        if location.usage in ['inventory', 'production', 'supplier']:
            return quants
        res_qty = qty
        if restrict_lot_id:
            if not preferred_domain_list:
                preferred_domain_list = [[('lot_id', '=', restrict_lot_id)], [('lot_id', '=', False)]]
            else:
                lot_list = []
                no_lot_list = []
                for pref_domain in preferred_domain_list:
                    pref_lot_domain = pref_domain + [('lot_id', '=', restrict_lot_id)]
                    pref_no_lot_domain = pref_domain + [('lot_id', '=', False)]
                    lot_list.append(pref_lot_domain)
                    no_lot_list.append(pref_no_lot_domain)
                preferred_domain_list = lot_list + no_lot_list

        if not preferred_domain_list:
            return self.quants_get(cr, uid, qty, move, ops=ops, domain=domain, removal_strategy=removal_strategy, context=context)
        for preferred_domain in preferred_domain_list:
            res_qty_cmp = float_compare(res_qty, 0, precision_rounding=product.uom_id.rounding)
            if res_qty_cmp > 0:
                #try to replace the last tuple (None, res_qty) with something that wasn't chosen at first because of the preferred order
                quants.pop()
                tmp_quants = self.quants_get(cr, uid, res_qty, move, ops=ops, domain=domain + preferred_domain,
                                             removal_strategy=removal_strategy, context=context)
                for quant in tmp_quants:
                    if quant[0]:
                        res_qty -= quant[1]
                quants += tmp_quants
        return quants

    def quants_get(self, cr, uid, qty, move, ops=False, domain=None, removal_strategy='fifo', context=None):
        """
        Use the removal strategies of product to search for the correct quants
        If you inherit, put the super at the end of your method.

        :location: browse record of the parent location where the quants have to be found
        :product: browse record of the product to find
        :qty in UoM of product
        """
        domain = domain or [('qty', '>', 0.0)]
        return self.apply_removal_strategy(cr, uid, qty, move, ops=ops, domain=domain, removal_strategy=removal_strategy, context=context)

    def apply_removal_strategy(self, cr, uid, quantity, move, ops=False, domain=None, removal_strategy='fifo', context=None):
        if removal_strategy == 'fifo':
            order = 'in_date, id'
            return self._quants_get_order(cr, uid, quantity, move, ops=ops, domain=domain, orderby=order, context=context)
        elif removal_strategy == 'lifo':
            order = 'in_date desc, id desc'
            return self._quants_get_order(cr, uid, quantity, move, ops=ops, domain=domain, orderby=order, context=context)
        raise UserError(_('Removal strategy %s not implemented.') % (removal_strategy,))

    def _quant_create(self, cr, uid, qty, move, lot_id=False, owner_id=False, src_package_id=False, dest_package_id=False,
                      force_location_from=False, force_location_to=False, context=None):
        '''Create a quant in the destination location and create a negative quant in the source location if it's an internal location.
        '''
        if context is None:
            context = {}
        price_unit = self.pool.get('stock.move').get_price_unit(cr, uid, move, context=context)
        location = force_location_to or move.location_dest_id
        rounding = move.product_id.uom_id.rounding
        vals = {
            'product_id': move.product_id.id,
            'location_id': location.id,
            'qty': float_round(qty, precision_rounding=rounding),
            'cost': price_unit,
            'history_ids': [(4, move.id)],
            'in_date': datetime.now().strftime(DEFAULT_SERVER_DATETIME_FORMAT),
            'company_id': move.company_id.id,
            'lot_id': lot_id,
            'owner_id': owner_id,
            'package_id': dest_package_id,
        }
        if move.location_id.usage == 'internal':
            #if we were trying to move something from an internal location and reach here (quant creation),
            #it means that a negative quant has to be created as well.
            negative_vals = vals.copy()
            negative_vals['location_id'] = force_location_from and force_location_from.id or move.location_id.id
            negative_vals['qty'] = float_round(-qty, precision_rounding=rounding)
            negative_vals['cost'] = price_unit
            negative_vals['negative_move_id'] = move.id
            negative_vals['package_id'] = src_package_id
            negative_quant_id = self.create(cr, SUPERUSER_ID, negative_vals, context=context)
            vals.update({'propagated_from_id': negative_quant_id})

        picking_type = move.picking_id and move.picking_id.picking_type_id or False
        if lot_id and move.product_id.tracking == 'serial' and (not picking_type or (picking_type.use_create_lots or picking_type.use_existing_lots)):
            if qty != 1.0:
                raise UserError(_('You should only receive by the piece with the same serial number'))

        #create the quant as superuser, because we want to restrict the creation of quant manually: we should always use this method to create quants
        quant_id = self.create(cr, SUPERUSER_ID, vals, context=context)
        return self.browse(cr, uid, quant_id, context=context)

    def _quant_split(self, cr, uid, quant, qty, context=None):
        context = context or {}
        rounding = quant.product_id.uom_id.rounding
        if float_compare(abs(quant.qty), abs(qty), precision_rounding=rounding) <= 0: # if quant <= qty in abs, take it entirely
            return False
        qty_round = float_round(qty, precision_rounding=rounding)
        new_qty_round = float_round(quant.qty - qty, precision_rounding=rounding)
        # Fetch the history_ids manually as it will not do a join with the stock moves then (=> a lot faster)
        cr.execute("""SELECT move_id FROM stock_quant_move_rel WHERE quant_id = %s""", (quant.id,))
        res = cr.fetchall()
        new_quant = self.copy(cr, SUPERUSER_ID, quant.id, default={'qty': new_qty_round, 'history_ids': [(4, x[0]) for x in res]}, context=context)
        self.write(cr, SUPERUSER_ID, quant.id, {'qty': qty_round}, context=context)
        return self.browse(cr, uid, new_quant, context=context)

    def _get_latest_move(self, cr, uid, quant, context=None):
        move = False
        for m in quant.history_ids:
            if not move or m.date > move.date:
                move = m
        return move

    @api.cr_uid_ids_context
    def _quants_merge(self, cr, uid, solved_quant_ids, solving_quant, context=None):
        path = []
        for move in solving_quant.history_ids:
            path.append((4, move.id))
        self.write(cr, SUPERUSER_ID, solved_quant_ids, {'history_ids': path}, context=context)

    def _search_quants_to_reconcile(self, cr, uid, quant, context=None):
        """
            Searches negative quants to reconcile for where the quant to reconcile is put
        """
        dom = [('qty', '<', 0)]
        order = 'in_date'
        dom += [('location_id', 'child_of', quant.location_id.id), ('product_id', '=', quant.product_id.id),
                ('owner_id', '=', quant.owner_id.id)]
        if quant.package_id.id:
            dom += [('package_id', '=', quant.package_id.id)]
        if quant.lot_id:
            dom += ['|', ('lot_id', '=', False), ('lot_id', '=', quant.lot_id.id)]
            order = 'lot_id, in_date'
        # Do not let the quant eat itself, or it will kill its history (e.g. returns / Stock -> Stock)
        dom += [('id', '!=', quant.propagated_from_id.id)]
        quants_search = self.search(cr, uid, dom, order=order, context=context)
        product = quant.product_id
        quants = []
        quantity = quant.qty
        for quant in self.browse(cr, uid, quants_search, context=context):
            rounding = product.uom_id.rounding
            if float_compare(quantity, abs(quant.qty), precision_rounding=rounding) >= 0:
                quants += [(quant, abs(quant.qty))]
                quantity -= abs(quant.qty)
            elif float_compare(quantity, 0.0, precision_rounding=rounding) != 0:
                quants += [(quant, quantity)]
                quantity = 0
                break
        return quants

    def _quant_reconcile_negative(self, cr, uid, quant, move, context=None):
        """
            When new quant arrive in a location, try to reconcile it with
            negative quants. If it's possible, apply the cost of the new
            quant to the counterpart of the negative quant.
        """
        context = context or {}
        context = dict(context)
        context.update({'force_unlink': True})
        solving_quant = quant
        quants = self._search_quants_to_reconcile(cr, uid, quant, context=context)
        product_uom_rounding = quant.product_id.uom_id.rounding
        for quant_neg, qty in quants:
            if not quant_neg or not solving_quant:
                continue
            to_solve_quant_ids = self.search(cr, uid, [('propagated_from_id', '=', quant_neg.id)], context=context)
            if not to_solve_quant_ids:
                continue
            solving_qty = qty
            solved_quant_ids = []
            for to_solve_quant in self.browse(cr, uid, to_solve_quant_ids, context=context):
                if float_compare(solving_qty, 0, precision_rounding=product_uom_rounding) <= 0:
                    continue
                solved_quant_ids.append(to_solve_quant.id)
                self._quant_split(cr, uid, to_solve_quant, min(solving_qty, to_solve_quant.qty), context=context)
                solving_qty -= min(solving_qty, to_solve_quant.qty)
            remaining_solving_quant = self._quant_split(cr, uid, solving_quant, qty, context=context)
            remaining_neg_quant = self._quant_split(cr, uid, quant_neg, -qty, context=context)
            #if the reconciliation was not complete, we need to link together the remaining parts
            if remaining_neg_quant:
                remaining_to_solve_quant_ids = self.search(cr, uid, [('propagated_from_id', '=', quant_neg.id), ('id', 'not in', solved_quant_ids)], context=context)
                if remaining_to_solve_quant_ids:
                    self.write(cr, SUPERUSER_ID, remaining_to_solve_quant_ids, {'propagated_from_id': remaining_neg_quant.id}, context=context)
            if solving_quant.propagated_from_id and solved_quant_ids:
                self.write(cr, SUPERUSER_ID, solved_quant_ids, {'propagated_from_id': solving_quant.propagated_from_id.id}, context=context)
            #delete the reconciled quants, as it is replaced by the solved quants
            self.unlink(cr, SUPERUSER_ID, [quant_neg.id], context=context)
            if solved_quant_ids:
                #price update + accounting entries adjustments
                self._price_update(cr, uid, solved_quant_ids, solving_quant.cost, context=context)
                #merge history (and cost?)
                self._quants_merge(cr, uid, solved_quant_ids, solving_quant, context=context)
            self.unlink(cr, SUPERUSER_ID, [solving_quant.id], context=context)
            solving_quant = remaining_solving_quant

    def _price_update(self, cr, uid, ids, newprice, context=None):
        self.write(cr, SUPERUSER_ID, ids, {'cost': newprice}, context=context)

    def quants_unreserve(self, cr, uid, move, context=None):
        related_quants = [x.id for x in move.reserved_quant_ids]
        if related_quants:
            #if move has a picking_id, write on that picking that pack_operation might have changed and need to be recomputed
            if move.partially_available:
                self.pool.get("stock.move").write(cr, uid, [move.id], {'partially_available': False}, context=context)
            self.write(cr, SUPERUSER_ID, related_quants, {'reservation_id': False}, context=context)

    def _quants_get_order(self, cr, uid, quantity, move, ops=False, domain=[], orderby='in_date', context=None):
        ''' Implementation of removal strategies
            If it can not reserve, it will return a tuple (None, qty)
        '''
        if context is None:
            context = {}
        product = move.product_id
        res = []
        offset = 0
        while float_compare(quantity, 0, precision_rounding=product.uom_id.rounding) > 0:
            quants = self.search(cr, uid, domain, order=orderby, limit=10, offset=offset, context=context)
            if not quants:
                res.append((None, quantity))
                break
            for quant in self.browse(cr, uid, quants, context=context):
                rounding = product.uom_id.rounding
                if float_compare(quantity, abs(quant.qty), precision_rounding=rounding) >= 0:
                    res += [(quant, abs(quant.qty))]
                    quantity -= abs(quant.qty)
                elif float_compare(quantity, 0.0, precision_rounding=rounding) != 0:
                    res += [(quant, quantity)]
                    quantity = 0
                    break
            offset += 10
        return res

class stock_picking(models.Model):
    _inherit = 'stock.picking'

    #quant_reserved_exist = fields.function(_get_quant_reserved_exist, type='boolean', string='Has quants already reserved', help='Check the existance of quants linked to this picking'),

    def _prepare_pack_ops(self, cr, uid, picking, quants, forced_qties, context=None):
        """ returns a list of dict, ready to be used in create() of stock.pack.operation.

        :param picking: browse record (stock.picking)
        :param quants: browse record list (stock.quant). List of quants associated to the picking
        :param forced_qties: dictionary showing for each product (keys) its corresponding quantity (value) that is not covered by the quants associated to the picking
        """
        def _picking_putaway_apply(product):
            location = False
            # Search putaway strategy
            if product_putaway_strats.get(product.id):
                location = product_putaway_strats[product.id]
            else:
                location = self.pool.get('stock.location').get_putaway_strategy(cr, uid, picking.location_dest_id, product, context=context)
                product_putaway_strats[product.id] = location
            return location or picking.location_dest_id.id

        # If we encounter an UoM that is smaller than the default UoM or the one already chosen, use the new one instead.
        product_uom = {} # Determines UoM used in pack operations
        location_dest_id = None
        location_id = None
        for move in [x for x in picking.move_lines if x.state not in ('done', 'cancel')]:
            if not product_uom.get(move.product_id.id):
                product_uom[move.product_id.id] = move.product_id.uom_id
            if move.product_uom.id != move.product_id.uom_id.id and move.product_uom.factor > product_uom[move.product_id.id].factor:
                product_uom[move.product_id.id] = move.product_uom
            if not move.scrapped:
                if location_dest_id and move.location_dest_id.id != location_dest_id:
                    raise UserError(_('The destination location must be the same for all the moves of the picking.'))
                location_dest_id = move.location_dest_id.id
                if location_id and move.location_id.id != location_id:
                    raise UserError(_('The source location must be the same for all the moves of the picking.'))
                location_id = move.location_id.id

        pack_obj = self.pool.get("stock.quant.package")
        quant_obj = self.pool.get("stock.quant")
        vals = []
        qtys_grouped = {}
        lots_grouped = {}
        #for each quant of the picking, find the suggested location
        quants_suggested_locations = {}
        product_putaway_strats = {}
        for quant in quants:
            if quant.qty <= 0:
                continue
            suggested_location_id = _picking_putaway_apply(quant.product_id)
            quants_suggested_locations[quant] = suggested_location_id

        #find the packages we can movei as a whole
        top_lvl_packages = self._get_top_level_packages(cr, uid, quants_suggested_locations, context=context)
        # and then create pack operations for the top-level packages found
        for pack in top_lvl_packages:
            pack_quant_ids = pack_obj.get_content(cr, uid, [pack.id], context=context)
            pack_quants = quant_obj.browse(cr, uid, pack_quant_ids, context=context)
            vals.append({
                    'picking_id': picking.id,
                    'package_id': pack.id,
                    'product_qty': 1.0,
                    'location_id': pack.location_id.id,
                    'location_dest_id': quants_suggested_locations[pack_quants[0]],
                    'owner_id': pack.owner_id.id,
                })
            #remove the quants inside the package so that they are excluded from the rest of the computation
            for quant in pack_quants:
                del quants_suggested_locations[quant]
        # Go through all remaining reserved quants and group by product, package, owner, source location and dest location
        # Lots will go into pack operation lot object
        for quant, dest_location_id in quants_suggested_locations.items():
            key = (quant.product_id.id, quant.package_id.id, quant.owner_id.id, quant.location_id.id, dest_location_id)
            if qtys_grouped.get(key):
                qtys_grouped[key] += quant.qty
            else:
                qtys_grouped[key] = quant.qty
            if quant.product_id.tracking != 'none' and quant.lot_id:
                lots_grouped.setdefault(key, {}).setdefault(quant.lot_id.id, 0.0)
                lots_grouped[key][quant.lot_id.id] += quant.qty

        # Do the same for the forced quantities (in cases of force_assign or incomming shipment for example)
        for product, qty in forced_qties.items():
            if qty <= 0:
                continue
            suggested_location_id = _picking_putaway_apply(product)
            key = (product.id, False, picking.owner_id.id, picking.location_id.id, suggested_location_id)
            if qtys_grouped.get(key):
                qtys_grouped[key] += qty
            else:
                qtys_grouped[key] = qty

        # Create the necessary operations for the grouped quants and remaining qtys
        uom_obj = self.pool.get('product.uom')
        prevals = {}
        for key, qty in qtys_grouped.items():
            product = self.pool.get("product.product").browse(cr, uid, key[0], context=context)
            uom_id = product.uom_id.id
            qty_uom = qty
            if product_uom.get(key[0]):
                uom_id = product_uom[key[0]].id
                qty_uom = uom_obj._compute_qty(cr, uid, product.uom_id.id, qty, uom_id)
            pack_lot_ids = []
            if lots_grouped.get(key):
                for lot in lots_grouped[key].keys():
                    pack_lot_ids += [(0, 0, {'lot_id': lot, 'qty': 0.0, 'qty_todo': lots_grouped[key][lot]})]
            val_dict = {
                'picking_id': picking.id,
                'product_qty': qty_uom,
                'product_id': key[0],
                'package_id': key[1],
                'owner_id': key[2],
                'location_id': key[3],
                'location_dest_id': key[4],
                'product_uom_id': uom_id,
                'pack_lot_ids': pack_lot_ids,
            }
            if key[0] in prevals:
                prevals[key[0]].append(val_dict)
            else:
                prevals[key[0]] = [val_dict]
        # prevals var holds the operations in order to create them in the same order than the picking stock moves if possible
        processed_products = set()
        for move in [x for x in picking.move_lines if x.state not in ('done', 'cancel')]:
            if move.product_id.id not in processed_products:
                vals += prevals.get(move.product_id.id, [])
                processed_products.add(move.product_id.id)
        return vals

    @api.cr_uid_ids_context
    def do_prepare_partial(self, cr, uid, picking_ids, context=None):
        context = context or {}
        pack_operation_obj = self.pool.get('stock.pack.operation')

        #get list of existing operations and delete them
        existing_package_ids = pack_operation_obj.search(cr, uid, [('picking_id', 'in', picking_ids)], context=context)
        if existing_package_ids:
            pack_operation_obj.unlink(cr, uid, existing_package_ids, context)
        for picking in self.browse(cr, uid, picking_ids, context=context):
            forced_qties = {}  # Quantity remaining after calculating reserved quants
            picking_quants = []
            #Calculate packages, reserved quants, qtys of this picking's moves
            for move in picking.move_lines:
                if move.state not in ('assigned', 'confirmed', 'waiting'):
                    continue
                move_quants = move.reserved_quant_ids
                picking_quants += move_quants
                forced_qty = (move.state == 'assigned') and move.product_qty - sum([x.qty for x in move_quants]) or 0
                #if we used force_assign() on the move, or if the move is incoming, forced_qty > 0
                if float_compare(forced_qty, 0, precision_rounding=move.product_id.uom_id.rounding) > 0:
                    if forced_qties.get(move.product_id):
                        forced_qties[move.product_id] += forced_qty
                    else:
                        forced_qties[move.product_id] = forced_qty
            for vals in self._prepare_pack_ops(cr, uid, picking, picking_quants, forced_qties, context=context):
                vals['fresh_record'] = False
                pack_operation_obj.create(cr, uid, vals, context=context)
        #recompute the remaining quantities all at once
        self.do_recompute_remaining_quantities(cr, uid, picking_ids, context=context)
        self.write(cr, uid, picking_ids, {'recompute_pack_op': False}, context=context)

    @api.cr_uid_ids_context
    def do_unreserve(self, cr, uid, picking_ids, context=None):
        """
          Will remove all quants for picking in picking_ids
        """
        moves_to_unreserve = []
        pack_line_to_unreserve = []
        for picking in self.browse(cr, uid, picking_ids, context=context):
            moves_to_unreserve += [m.id for m in picking.move_lines if m.state not in ('done', 'cancel')]
            pack_line_to_unreserve += [p.id for p in picking.pack_operation_ids]
        if moves_to_unreserve:
            if pack_line_to_unreserve:
                self.pool.get('stock.pack.operation').unlink(cr, uid, pack_line_to_unreserve, context=context)
            self.pool.get('stock.move').do_unreserve(cr, uid, moves_to_unreserve, context=context)

    def recompute_remaining_qty(self, cr, uid, picking, done_qtys=False, context=None):
        def _create_link_for_index(operation_id, index, product_id, qty_to_assign, quant_id=False):
            move_dict = prod2move_ids[product_id][index]
            qty_on_link = min(move_dict['remaining_qty'], qty_to_assign)
            self.pool.get('stock.move.operation.link').create(cr, uid, {'move_id': move_dict['move'].id, 'operation_id': operation_id, 'qty': qty_on_link, 'reserved_quant_id': quant_id}, context=context)
            if move_dict['remaining_qty'] == qty_on_link:
                prod2move_ids[product_id].pop(index)
            else:
                move_dict['remaining_qty'] -= qty_on_link
            return qty_on_link

        def _create_link_for_quant(operation_id, quant, qty):
            """create a link for given operation and reserved move of given quant, for the max quantity possible, and returns this quantity"""
            if not quant.reservation_id.id:
                return _create_link_for_product(operation_id, quant.product_id.id, qty)
            qty_on_link = 0
            for i in range(0, len(prod2move_ids[quant.product_id.id])):
                if prod2move_ids[quant.product_id.id][i]['move'].id != quant.reservation_id.id:
                    continue
                qty_on_link = _create_link_for_index(operation_id, i, quant.product_id.id, qty, quant_id=quant.id)
                break
            return qty_on_link

        def _create_link_for_product(operation_id, product_id, qty):
            '''method that creates the link between a given operation and move(s) of given product, for the given quantity.
            Returns True if it was possible to create links for the requested quantity (False if there was not enough quantity on stock moves)'''
            qty_to_assign = qty
            prod_obj = self.pool.get("product.product")
            product = prod_obj.browse(cr, uid, product_id)
            rounding = product.uom_id.rounding
            qtyassign_cmp = float_compare(qty_to_assign, 0.0, precision_rounding=rounding)
            if prod2move_ids.get(product_id):
                while prod2move_ids[product_id] and qtyassign_cmp > 0:
                    qty_on_link = _create_link_for_index(operation_id, 0, product_id, qty_to_assign, quant_id=False)
                    qty_to_assign -= qty_on_link
                    qtyassign_cmp = float_compare(qty_to_assign, 0.0, precision_rounding=rounding)
            return qtyassign_cmp == 0

        uom_obj = self.pool.get('product.uom')
        package_obj = self.pool.get('stock.quant.package')
        quant_obj = self.pool.get('stock.quant')
        link_obj = self.pool.get('stock.move.operation.link')
        quants_in_package_done = set()
        prod2move_ids = {}
        still_to_do = []
        #make a dictionary giving for each product, the moves and related quantity that can be used in operation links
        moves = sorted([x for x in picking.move_lines if x.state not in ('done', 'cancel')], key=lambda x: (((x.state == 'assigned') and -2 or 0) + (x.partially_available and -1 or 0)))
        for move in moves:
            if not prod2move_ids.get(move.product_id.id):
                prod2move_ids[move.product_id.id] = [{'move': move, 'remaining_qty': move.product_qty}]
            else:
                prod2move_ids[move.product_id.id].append({'move': move, 'remaining_qty': move.product_qty})

        need_rereserve = False
        #sort the operations in order to give higher priority to those with a package, then a serial number
        operations = picking.pack_operation_ids
        operations = sorted(operations, key=lambda x: ((x.package_id and not x.product_id) and -4 or 0) + (x.package_id and -2 or 0) + (x.pack_lot_ids and -1 or 0))
        #delete existing operations to start again from scratch
        links = link_obj.search(cr, uid, [('operation_id', 'in', [x.id for x in operations])], context=context)
        if links:
            link_obj.unlink(cr, uid, links, context=context)
        #1) first, try to create links when quants can be identified without any doubt
        for ops in operations:
            lot_qty = {}
            for packlot in ops.pack_lot_ids:
                lot_qty[packlot.lot_id.id] = uom_obj._compute_qty(cr, uid, ops.product_uom_id.id, packlot.qty, ops.product_id.uom_id.id)
            #for each operation, create the links with the stock move by seeking on the matching reserved quants,
            #and deffer the operation if there is some ambiguity on the move to select
            if ops.package_id and not ops.product_id and (not done_qtys or ops.qty_done):
                #entire package
                quant_ids = package_obj.get_content(cr, uid, [ops.package_id.id], context=context)
                for quant in quant_obj.browse(cr, uid, quant_ids, context=context):
                    remaining_qty_on_quant = quant.qty
                    if quant.reservation_id:
                        #avoid quants being counted twice
                        quants_in_package_done.add(quant.id)
                        qty_on_link = _create_link_for_quant(ops.id, quant, quant.qty)
                        remaining_qty_on_quant -= qty_on_link
                    if remaining_qty_on_quant:
                        still_to_do.append((ops, quant.product_id.id, remaining_qty_on_quant))
                        need_rereserve = True
            elif ops.product_id.id:
                #Check moves with same product
                product_qty = ops.qty_done if done_qtys else ops.product_qty
                qty_to_assign = uom_obj._compute_qty_obj(cr, uid, ops.product_uom_id, product_qty, ops.product_id.uom_id, context=context)
                precision_rounding = ops.product_id.uom_id.rounding
                for move_dict in prod2move_ids.get(ops.product_id.id, []):
                    move = move_dict['move']
                    for quant in move.reserved_quant_ids:
                        if float_compare(qty_to_assign, 0, precision_rounding=precision_rounding) != 1:
                            break
                        if quant.id in quants_in_package_done:
                            continue

                        #check if the quant is matching the operation details
                        if ops.package_id:
                            flag = quant.package_id and bool(package_obj.search(cr, uid, [('id', 'child_of', [ops.package_id.id])], context=context)) or False
                        else:
                            flag = not quant.package_id.id
                        flag = flag and (ops.owner_id.id == quant.owner_id.id)
                        if flag:
                            if not lot_qty:
                                max_qty_on_link = min(quant.qty, qty_to_assign)
                                qty_on_link = _create_link_for_quant(ops.id, quant, max_qty_on_link)
                                qty_to_assign -= qty_on_link
                            else:
                                if lot_qty.get(quant.lot_id.id): #if there is still some qty left
                                    max_qty_on_link = min(quant.qty, qty_to_assign, lot_qty[quant.lot_id.id])
                                    qty_on_link = _create_link_for_quant(ops.id, quant, max_qty_on_link)
                                    qty_to_assign -= qty_on_link
                                    lot_qty[quant.lot_id.id] -= qty_on_link

                qty_assign_cmp = float_compare(qty_to_assign, 0, precision_rounding=precision_rounding)
                if qty_assign_cmp > 0:
                    #qty reserved is less than qty put in operations. We need to create a link but it's deferred after we processed
                    #all the quants (because they leave no choice on their related move and needs to be processed with higher priority)
                    still_to_do += [(ops, ops.product_id.id, qty_to_assign)]
                    need_rereserve = True

        #2) then, process the remaining part
        all_op_processed = True
        for ops, product_id, remaining_qty in still_to_do:
            all_op_processed = _create_link_for_product(ops.id, product_id, remaining_qty) and all_op_processed
        return (need_rereserve, all_op_processed)

    def picking_recompute_remaining_quantities(self, cr, uid, picking, done_qtys=False, context=None):
        need_rereserve = False
        all_op_processed = True
        if picking.pack_operation_ids:
            need_rereserve, all_op_processed = self.recompute_remaining_qty(cr, uid, picking, done_qtys=done_qtys, context=context)
        return need_rereserve, all_op_processed

    @api.cr_uid_ids_context
    def do_recompute_remaining_quantities(self, cr, uid, picking_ids, done_qtys=False, context=None):
        for picking in self.browse(cr, uid, picking_ids, context=context):
            if picking.pack_operation_ids:
                self.recompute_remaining_qty(cr, uid, picking, done_qtys=done_qtys, context=context)

    def _prepare_values_extra_move(self, cr, uid, op, product, remaining_qty, context=None):
        """
        Creates an extra move when there is no corresponding original move to be copied
        """
        uom_obj = self.pool.get("product.uom")
        uom_id = product.uom_id.id
        qty = remaining_qty
        if op.product_id and op.product_uom_id and op.product_uom_id.id != product.uom_id.id:
            if op.product_uom_id.factor > product.uom_id.factor: #If the pack operation's is a smaller unit
                uom_id = op.product_uom_id.id
                #HALF-UP rounding as only rounding errors will be because of propagation of error from default UoM
                qty = uom_obj._compute_qty_obj(cr, uid, product.uom_id, remaining_qty, op.product_uom_id, rounding_method='HALF-UP')
        picking = op.picking_id
        ref = product.default_code
        name = '[' + ref + ']' + ' ' + product.name if ref else product.name
        proc_id = False
        for m in op.linked_move_operation_ids:
            if m.move_id.procurement_id:
                proc_id = m.move_id.procurement_id.id
                break
        res = {
            'picking_id': picking.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'product_id': product.id,
            'procurement_id': proc_id,
            'product_uom': uom_id,
            'product_uom_qty': qty,
            'name': _('Extra Move: ') + name,
            'state': 'draft',
            'restrict_partner_id': op.owner_id.id,
            'group_id': picking.group_id.id,
            }
        return res

    def _create_extra_moves(self, cr, uid, picking, context=None):
        '''This function creates move lines on a picking, at the time of do_transfer, based on
        unexpected product transfers (or exceeding quantities) found in the pack operations.
        '''
        move_obj = self.pool.get('stock.move')
        operation_obj = self.pool.get('stock.pack.operation')
        moves = []
        for op in picking.pack_operation_ids:
            for product, remaining_qty in operation_obj._get_remaining_prod_quantities(cr, uid, op, context=context).items():
                if float_compare(remaining_qty, 0, precision_rounding=product.uom_id.rounding) > 0:
                    vals = self._prepare_values_extra_move(cr, uid, op, product, remaining_qty, context=context)
                    moves.append(move_obj.create(cr, uid, vals, context=context))
        if moves:
            move_obj.action_confirm(cr, uid, moves, context=context)
        return moves

    def do_new_transfer(self, cr, uid, ids, context=None):
        pack_op_obj = self.pool['stock.pack.operation']
        data_obj = self.pool['ir.model.data']
        for pick in self.browse(cr, uid, ids, context=context):
            to_delete = []
            if not pick.move_lines and not pick.pack_operation_ids:
                raise UserError(_('Please create some Initial Demand or Mark as Todo and create some Operations. '))
            # In draft or with no pack operations edited yet, ask if we can just do everything
            if pick.state == 'draft' or all([x.qty_done == 0.0 for x in pick.pack_operation_ids]):
                # If no lots when needed, raise error
                picking_type = pick.picking_type_id
                if (picking_type.use_create_lots or picking_type.use_existing_lots):
                    for pack in pick.pack_operation_ids:
                        if pack.product_id and pack.product_id.tracking != 'none':
                            raise UserError(_('Some products require lots, so you need to specify those first!'))
                view = data_obj.xmlid_to_res_id(cr, uid, 'stock.view_immediate_transfer')
                wiz_id = self.pool['stock.immediate.transfer'].create(cr, uid, {'pick_id': pick.id}, context=context)
                return {
                     'name': _('Immediate Transfer?'),
                     'type': 'ir.actions.act_window',
                     'view_type': 'form',
                     'view_mode': 'form',
                     'res_model': 'stock.immediate.transfer',
                     'views': [(view, 'form')],
                     'view_id': view,
                     'target': 'new',
                     'res_id': wiz_id,
                     'context': context,
                 }

            # Check backorder should check for other barcodes
            if self.check_backorder(cr, uid, pick, context=context):
                view = data_obj.xmlid_to_res_id(cr, uid, 'stock.view_backorder_confirmation')
                wiz_id = self.pool['stock.backorder.confirmation'].create(cr, uid, {'pick_id': pick.id}, context=context)
                return {
                         'name': _('Create Backorder?'),
                         'type': 'ir.actions.act_window',
                         'view_type': 'form',
                         'view_mode': 'form',
                         'res_model': 'stock.backorder.confirmation',
                         'views': [(view, 'form')],
                         'view_id': view,
                         'target': 'new',
                         'res_id': wiz_id,
                         'context': context,
                     }
            for operation in pick.pack_operation_ids:
                if operation.qty_done < 0:
                    raise UserError(_('No negative quantities allowed'))
                if operation.qty_done > 0:
                    pack_op_obj.write(cr, uid, operation.id, {'product_qty': operation.qty_done}, context=context)
                else:
                    to_delete.append(operation.id)
            if to_delete:
                pack_op_obj.unlink(cr, uid, to_delete, context=context)
        self.do_transfer(cr, uid, ids, context=context)
        return

    def check_backorder(self, cr, uid, picking, context=None):
        need_rereserve, all_op_processed = self.picking_recompute_remaining_quantities(cr, uid, picking, done_qtys=True, context=context)
        for move in picking.move_lines:
            if float_compare(move.remaining_qty, 0, precision_rounding = move.product_id.uom_id.rounding) != 0:
                return True
        return False

    def do_transfer(self, cr, uid, ids, context=None):
        """
            If no pack operation, we do simple action_done of the picking
            Otherwise, do the pack operations
        """
        if not context:
            context = {}
        notrack_context = dict(context, mail_notrack=True)
        stock_move_obj = self.pool.get('stock.move')
        self.create_lots_for_picking(cr, uid, ids, context=context)
        for picking in self.browse(cr, uid, ids, context=context):
            if not picking.pack_operation_ids:
                self.action_done(cr, uid, [picking.id], context=context)
                continue
            else:
                need_rereserve, all_op_processed = self.picking_recompute_remaining_quantities(cr, uid, picking, context=context)
                #create extra moves in the picking (unexpected product moves coming from pack operations)
                todo_move_ids = []
                if not all_op_processed:
                    todo_move_ids += self._create_extra_moves(cr, uid, picking, context=context)
                if need_rereserve or not all_op_processed: 
                    moves_reassign = any(x.origin_returned_move_id or x.move_orig_ids for x in picking.move_lines if x.state not in ['done', 'cancel'])
                    if moves_reassign and (picking.location_id.usage not in ("supplier", "production", "inventory")):
                        ctx = dict(context)
                        ctx['reserve_only_ops'] = True #unnecessary to assign other quants than those involved with pack operations as they will be unreserved anyways.
                        ctx['no_state_change'] = True
                        self.rereserve_quants(cr, uid, picking, move_ids=picking.move_lines.ids, context=ctx)
                    self.do_recompute_remaining_quantities(cr, uid, [picking.id], context=context)

                #split move lines if needed
                toassign_move_ids = []
                for move in picking.move_lines:
                    remaining_qty = move.remaining_qty
                    if move.state in ('done', 'cancel'):
                        #ignore stock moves cancelled or already done
                        continue
                    elif move.state == 'draft':
                        toassign_move_ids.append(move.id)
                    if float_compare(remaining_qty, 0,  precision_rounding = move.product_id.uom_id.rounding) == 0:
                        if move.state in ('draft', 'assigned', 'confirmed'):
                            todo_move_ids.append(move.id)
                    elif float_compare(remaining_qty,0, precision_rounding = move.product_id.uom_id.rounding) > 0 and \
                                float_compare(remaining_qty, move.product_qty, precision_rounding = move.product_id.uom_id.rounding) < 0:
                        new_move = stock_move_obj.split(cr, uid, move, remaining_qty, context=notrack_context)
                        todo_move_ids.append(move.id)
                        #Assign move as it was assigned before
                        toassign_move_ids.append(new_move)
                todo_move_ids = list(set(todo_move_ids))
                if todo_move_ids and not context.get('do_only_split'):
                    self.pool.get('stock.move').action_done(cr, uid, todo_move_ids, context=context)
                elif context.get('do_only_split'):
                    context = dict(context, split=todo_move_ids)
            self._create_backorder(cr, uid, picking, context=context)
        return True

    def put_in_pack(self, cr, uid, ids, context=None):
        stock_move_obj = self.pool["stock.move"]
        stock_operation_obj = self.pool["stock.pack.operation"]
        package_obj = self.pool["stock.quant.package"]
        package_id = False
        for pick in self.browse(cr, uid, ids, context=context):
            operations = [x for x in pick.pack_operation_ids if x.qty_done > 0 and (not x.result_package_id)]
            pack_operation_ids = []
            for operation in operations:
                #If we haven't done all qty in operation, we have to split into 2 operation
                op = operation
                if operation.qty_done < operation.product_qty:
                    new_operation = stock_operation_obj.copy(cr, uid, operation.id, {'product_qty': operation.qty_done,'qty_done': operation.qty_done}, context=context)

                    stock_operation_obj.write(cr, uid, operation.id, {'product_qty': operation.product_qty - operation.qty_done,'qty_done': 0}, context=context)
                    if operation.pack_lot_ids:
                        packlots_transfer = [(4, x.id) for x in operation.pack_lot_ids]
                        stock_operation_obj.write(cr, uid, [new_operation], {'pack_lot_ids': packlots_transfer}, context=context)

                        # the stock.pack.operation.lot records now belong to the new, packaged stock.pack.operation
                        # we have to create new ones with new quantities for our original, unfinished stock.pack.operation
                        stock_operation_obj._copy_remaining_pack_lot_ids(cr, uid, new_operation, operation.id, context=context)

                    op = stock_operation_obj.browse(cr, uid, new_operation, context=context)
                pack_operation_ids.append(op.id)
            if operations:
                stock_operation_obj.check_tracking(cr, uid, pack_operation_ids, context=context)
                package_id = package_obj.create(cr, uid, {}, context=context)
                stock_operation_obj.write(cr, uid, pack_operation_ids, {'result_package_id': package_id}, context=context)
            else:
                raise UserError(_('Please process some quantities to put in the pack first!'))
        return package_id

class stock_move(models.Model):
    _inherit = 'stock.move'

    def _quantity_normalize_second(self, cr, uid, ids, name, args, context=None):
        uom_obj = self.pool.get('product.uom')
        res = {}
        for m in self.browse(cr, uid, ids, context=context):
            res[m.id] = uom_obj._compute_qty_obj(cr, uid, m.product_uom, m.product_uom_qty, m.product_id.uom_id, context=context)
        return res

    def _get_remaining_qty(self, cr, uid, ids, field_name, args, context=None):
        uom_obj = self.pool.get('product.uom')
        res = {}
        for move in self.browse(cr, uid, ids, context=context):
            qty = move.product_qty
            for record in move.linked_move_operation_ids:
                qty -= record.qty
            # Keeping in product default UoM
            res[move.id] = float_round(qty, precision_rounding=move.product_id.uom_id.rounding)
        return res

    def _get_product_availability(self, cr, uid, ids, field_name, args, context=None):
        quant_obj = self.pool.get('stock.quant')
        res = dict.fromkeys(ids, False)
        for move in self.browse(cr, uid, ids, context=context):
            if move.state == 'done':
                res[move.id] = move.product_qty
            else:
                sublocation_ids = self.pool.get('stock.location').search(cr, uid, [('id', 'child_of', [move.location_id.id])], context=context)
                quant_ids = quant_obj.search(cr, uid, [('location_id', 'in', sublocation_ids), ('product_id', '=', move.product_id.id), ('reservation_id', '=', False)], context=context)
                availability = 0
                for quant in quant_obj.browse(cr, uid, quant_ids, context=context):
                    availability += quant.qty
                res[move.id] = min(move.product_qty, availability)
        return res

    def _get_string_qty_information(self, cr, uid, ids, field_name, args, context=None):
        uom_obj = self.pool.get('product.uom')
        res = dict.fromkeys(ids, '')
        precision = self.pool['decimal.precision'].precision_get(cr, uid, 'Product Unit of Measure')
        for move in self.browse(cr, uid, ids, context=context):
            if move.state in ('draft', 'done', 'cancel') or move.location_id.usage != 'internal':
                res[move.id] = ''  # 'not applicable' or 'n/a' could work too
                continue
            total_available = min(move.product_qty, move.reserved_availability + move.availability)
            total_available = uom_obj._compute_qty_obj(cr, uid, move.product_id.uom_id, total_available, move.product_uom, round=False, context=context)
            total_available = float_round(total_available, precision_digits=precision)
            info = str(total_available)
            #look in the settings if we need to display the UoM name or not
            if self.pool.get('res.users').has_group(cr, uid, 'product.group_uom'):
                info += ' ' + move.product_uom.name
            if move.reserved_availability:
                if move.reserved_availability != total_available:
                    #some of the available quantity is assigned and some are available but not reserved
                    reserved_available = uom_obj._compute_qty_obj(cr, uid, move.product_id.uom_id, move.reserved_availability, move.product_uom, round=False, context=context)
                    reserved_available = float_round(reserved_available, precision_digits=precision)
                    info += _(' (%s reserved)') % str(reserved_available)
                else:
                    #all available quantity is assigned
                    info += _(' (reserved)')
            res[move.id] = info
        return res
    def _set_product_qty(self, cr, uid, id, field, value, arg, context=None):
        """ The meaning of product_qty field changed lately and is now a functional field computing the quantity
            in the default product UoM. This code has been added to raise an error if a write is made given a value
            for `product_qty`, where the same write should set the `product_uom_qty` field instead, in order to
            detect errors.
        """
        raise UserError(_('The requested operation cannot be processed because of a programming error setting the `product_qty` field instead of the `product_uom_qty`.'))

    def _get_reserved_availability(self, cr, uid, ids, field_name, args, context=None):
        res = dict.fromkeys(ids, 0)
        for move in self.browse(cr, uid, ids, context=context):
            res[move.id] = sum([quant.qty for quant in move.reserved_quant_ids])
        return res
    
    second_product_qty = fields.Float(compute='_quantity_normalize_second', digits=0, string='Second Quantity',store=True ,help='Quantity in the default second UoM of the product')
    second_product_uom_qty = fields.Float('Second_Quantity', digits_compute=dp.get_precision('Product Unit of Measure'),required=False, states={'done': [('readonly', True)]},
            help="This is the quantity of products from an inventory "
                "point of view. For moves in the state 'done', this is the "
                "quantity of products that were actually moved. For other "
                "moves, this is the quantity of product that is planned to "
                "be moved. Lowering this quantity does not generate a "
                "backorder. Changing this quantity on assigned moves affects "
                "the product reservation, and should be done with care."
        )
    second_product_uom = fields.Many2one('product.uom', 'Second Unit of Measure', required=False, states={'done': [('readonly', True)]})

    #second_remaining_qty = fields.function(_get_remaining_qty, type='float', string='Second Remaining Quantity', digits=0,
                                         #states={'done': [('readonly', True)]}, help="Remaining Quantity in default UoM according to operations matched with this move")
    #reserved_availability = fields.function(_get_reserved_availability, type='float', string='Quantity Reserved', readonly=True, help='Quantity that has already been reserved for this move'),
    #availability = fields.function(_get_product_availability, type='float', string='Forecasted Quantity', readonly=True, help='Quantity in stock that can still be reserved for this move'),
    #string_availability_info = fields.function(_get_string_qty_information, type='text', string='Availability', readonly=True, help='Show various information on stock availability for this move')

    def _check_uom(self, cr, uid, ids, context=None):
        for move in self.browse(cr, uid, ids, context=context):
            if move.product_id.uom_id.category_id.id != move.product_uom.category_id.id:
                return False
        return True

    _constraints = [
        (_check_uom,
            'You try to move a product using a UoM that is not compatible with the UoM of the product moved. Please use an UoM in the same UoM category.',
            ['product_uom']),
    ]

    @api.cr_uid_ids_context
    def do_unreserve(self, cr, uid, move_ids, context=None):
        quant_obj = self.pool.get("stock.quant")
        for move in self.browse(cr, uid, move_ids, context=context):
            if move.state in ('done', 'cancel'):
                raise UserError(_('Cannot unreserve a done move'))
            quant_obj.quants_unreserve(cr, uid, move, context=context)
            if not context.get('no_state_change'):
                if self.find_move_ancestors(cr, uid, move, context=context):
                    self.write(cr, uid, [move.id], {'state': 'waiting'}, context=context)
                else:
                    self.write(cr, uid, [move.id], {'state': 'confirmed'}, context=context)

    def _prepare_procurement_from_move(self, cr, uid, move, context=None):
        origin = (move.group_id and (move.group_id.name + ":") or "") + (move.rule_id and move.rule_id.name or move.origin or move.picking_id.name or "/")
        group_id = move.group_id and move.group_id.id or False
        if move.rule_id:
            if move.rule_id.group_propagation_option == 'fixed' and move.rule_id.group_id:
                group_id = move.rule_id.group_id.id
            elif move.rule_id.group_propagation_option == 'none':
                group_id = False
        return {
            'name': move.rule_id and move.rule_id.name or "/",
            'origin': origin,
            'company_id': move.company_id and move.company_id.id or False,
            'date_planned': move.date,
            'product_id': move.product_id.id,
            'product_qty': move.product_uom_qty,
            'product_uom': move.product_uom.id,
            'location_id': move.location_id.id,
            'move_dest_id': move.id,
            'group_id': group_id,
            'route_ids': [(4, x.id) for x in move.route_ids],
            'warehouse_id': move.warehouse_id.id or (move.picking_type_id and move.picking_type_id.warehouse_id.id or False),
            'priority': move.priority,
        }

    def write(self, cr, uid, ids, vals, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        picking_obj = self.pool['stock.picking']
        # Check that we do not modify a stock.move which is done
        frozen_fields = set(['product_qty', 'product_uom', 'location_id', 'location_dest_id', 'product_id'])
        moves = self.browse(cr, uid, ids, context=context)
        for move in moves:
            if move.state == 'done':
                if frozen_fields.intersection(vals):
                    raise UserError(_('Quantities, Units of Measure, Products and Locations cannot be modified on stock moves that have already been processed (except by the Administrator).'))
        propagated_changes_dict = {}
        #propagation of quantity change
        if vals.get('product_uom_qty'):
            propagated_changes_dict['product_uom_qty'] = vals['product_uom_qty']
        if vals.get('product_uom_id'):
            propagated_changes_dict['product_uom_id'] = vals['product_uom_id']
        if vals.get('product_uos_qty'):
            propagated_changes_dict['product_uos_qty'] = vals['product_uos_qty']
        if vals.get('product_uos_id'):
            propagated_changes_dict['product_uos_id'] = vals['product_uos_id']
        #propagation of expected date:
        propagated_date_field = False
        if vals.get('date_expected'):
            #propagate any manual change of the expected date
            propagated_date_field = 'date_expected'
        elif (vals.get('state', '') == 'done' and vals.get('date')):
            #propagate also any delta observed when setting the move as done
            propagated_date_field = 'date'

        if not context.get('do_not_propagate', False) and (propagated_date_field or propagated_changes_dict):
            #any propagation is (maybe) needed
            for move in self.browse(cr, uid, ids, context=context):
                if move.move_dest_id and move.propagate:
                    if 'date_expected' in propagated_changes_dict:
                        propagated_changes_dict.pop('date_expected')
                    if propagated_date_field:
                        current_date = datetime.strptime(move.date_expected, DEFAULT_SERVER_DATETIME_FORMAT)
                        new_date = datetime.strptime(vals.get(propagated_date_field), DEFAULT_SERVER_DATETIME_FORMAT)
                        delta = new_date - current_date
                        if abs(delta.days) >= move.company_id.propagation_minimum_delta:
                            old_move_date = datetime.strptime(move.move_dest_id.date_expected, DEFAULT_SERVER_DATETIME_FORMAT)
                            new_move_date = (old_move_date + relativedelta.relativedelta(days=delta.days or 0)).strftime(DEFAULT_SERVER_DATETIME_FORMAT)
                            propagated_changes_dict['date_expected'] = new_move_date
                    #For pushed moves as well as for pulled moves, propagate by recursive call of write().
                    #Note that, for pulled moves we intentionally don't propagate on the procurement.
                    if propagated_changes_dict:
                        self.write(cr, uid, [move.move_dest_id.id], propagated_changes_dict, context=context)
        track_pickings = not context.get('mail_notrack') and any(field in vals for field in ['state', 'picking_id', 'partially_available'])
        if track_pickings:
            to_track_picking_ids = set([move.picking_id.id for move in moves if move.picking_id])
            if vals.get('picking_id'):
                to_track_picking_ids.add(vals['picking_id'])
            to_track_picking_ids = list(to_track_picking_ids)
            pickings = picking_obj.browse(cr, uid, to_track_picking_ids, context=context)
            initial_values = dict((picking.id, {'state': picking.state}) for picking in pickings)
        res = super(stock_move, self).write(cr, uid, ids, vals, context=context)
        if track_pickings:
            picking_obj.message_track(cr, uid, to_track_picking_ids, picking_obj.fields_get(cr, uid, ['state'], context=context), initial_values, context=context)
        return res
    def onchange_second_quantity(self, cr, uid, ids, product_id, second_product_qty, second_product_uom):
        """ On change of product quantity finds UoM
        @param product_id: Product id
        @param product_qty: Changed Quantity of product
        @param product_uom: Unit of measure of product
        @return: Dictionary of values
        """
        warning = {}
        result = {}

        if (not product_id) or (second_product_qty <= 0.0):
            result['second_product_qty'] = 0.0
            return {'value': result}

        product_obj = self.pool.get('product.product')
        # Warn if the quantity was decreased
        if ids:
            for move in self.read(cr, uid, ids, ['second_product_qty']):
                if second_product_qty < move['second_product_qty']:
                    warning.update({
                        'title': _('Information'),
                        'message': _("By changing this quantity here, you accept the "
                                "new quantity as complete: Odoo will not "
                                "automatically generate a back order.")})
                break
        return {'warning': warning}
    def onchange_quantity(self, cr, uid, ids, product_id, product_qty, product_uom):
        """ On change of product quantity finds UoM
        @param product_id: Product id
        @param product_qty: Changed Quantity of product
        @param product_uom: Unit of measure of product
        @return: Dictionary of values
        """
        warning = {}
        result = {}

        if (not product_id) or (product_qty <= 0.0):
            result['product_qty'] = 0.0
            return {'value': result}

        product_obj = self.pool.get('product.product')
        # Warn if the quantity was decreased
        if ids:
            for move in self.read(cr, uid, ids, ['product_qty']):
                if product_qty < move['product_qty']:
                    warning.update({
                        'title': _('Information'),
                        'message': _("By changing this quantity here, you accept the "
                                "new quantity as complete: Odoo will not "
                                "automatically generate a back order.")})
                break
        return {'warning': warning}

    def onchange_product_id(self, cr, uid, ids, prod_id=False, loc_id=False, loc_dest_id=False, partner_id=False):
        """ On change of product id, if finds UoM, quantity
        @param prod_id: Changed Product id
        @param loc_id: Source location id
        @param loc_dest_id: Destination location id
        @param partner_id: Address id of partner
        @return: Dictionary of values
        """
        if not prod_id:
            return {'domain': {'product_uom': []}}
        user = self.pool.get('res.users').browse(cr, uid, uid)
        lang = user and user.lang or False
        if partner_id:
            addr_rec = self.pool.get('res.partner').browse(cr, uid, partner_id)
            if addr_rec:
                lang = addr_rec and addr_rec.lang or False
        ctx = {'lang': lang}

        product = self.pool.get('product.product').browse(cr, uid, [prod_id], context=ctx)[0]
        result = {
            'name': product.partner_ref,
            'product_uom': product.uom_id.id,
            'product_uom_qty': 1.00,
        }
        if loc_id:
            result['location_id'] = loc_id
        if loc_dest_id:
            result['location_dest_id'] = loc_dest_id
        res = {'value': result,
               'domain': {'product_uom': [('category_id', '=', product.uom_id.category_id.id)]}
               }
        return res

    def action_confirm(self, cr, uid, ids, context=None):
        """ Confirms stock move or put it in waiting if it's linked to another move.
        @return: List of ids.
        """
        if not context:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        states = {
            'confirmed': [],
            'waiting': []
        }
        to_assign = {}
        for move in self.browse(cr, uid, ids, context=context):
            self.attribute_price(cr, uid, move, context=context)
            state = 'confirmed'
            #if the move is preceeded, then it's waiting (if preceeding move is done, then action_assign has been called already and its state is already available)
            if move.move_orig_ids:
                state = 'waiting'
            #if the move is split and some of the ancestor was preceeded, then it's waiting as well
            elif move.split_from:
                move2 = move.split_from
                while move2 and state != 'waiting':
                    if move2.move_orig_ids:
                        state = 'waiting'
                    move2 = move2.split_from
            states[state].append(move.id)

            if not move.picking_id and move.picking_type_id:
                key = (move.group_id.id, move.location_id.id, move.location_dest_id.id)
                if key not in to_assign:
                    to_assign[key] = []
                to_assign[key].append(move.id)
        moves = [move for move in self.browse(cr, uid, states['confirmed'], context=context) if move.procure_method == 'make_to_order']
        self._create_procurements(cr, uid, moves, context=context)
        for move in moves:
            states['waiting'].append(move.id)
            states['confirmed'].remove(move.id)

        for state, write_ids in states.items():
            if len(write_ids):
                self.write(cr, uid, write_ids, {'state': state}, context=context)
        #assign picking in batch for all confirmed move that share the same details
        for key, move_ids in to_assign.items():
            self._picking_assign(cr, uid, move_ids, context=context)
        moves = self.browse(cr, uid, ids, context=context)
        self._push_apply(cr, uid, moves, context=context)
        return ids

    def check_recompute_pack_op(self, cr, uid, ids, context=None):
        pickings = list(set([x.picking_id for x in self.browse(cr, uid, ids, context=context) if x.picking_id]))
        pickings_partial = []
        pickings_write = []
        pick_obj = self.pool['stock.picking']
        for pick in pickings:
            if pick.state in ('waiting', 'confirmed'): #In case of 'all at once' delivery method it should not prepare pack operations
                continue
            # Check if someone was treating the picking already
            if not any([x.qty_done > 0 for x in pick.pack_operation_ids]):
                pickings_partial.append(pick.id)
            else:
                pickings_write.append(pick.id)
        if pickings_partial:
            pick_obj.do_prepare_partial(cr, uid, pickings_partial, context=context)
        if pickings_write:
            pick_obj.write(cr, uid, pickings_write, {'recompute_pack_op': True}, context=context)

    def action_assign(self, cr, uid, ids, no_prepare=False, context=None):
        """ Checks the product type and accordingly writes the state.
        """
        context = context or {}
        quant_obj = self.pool.get("stock.quant")
        uom_obj = self.pool['product.uom']
        to_assign_moves = set()
        main_domain = {}
        todo_moves = []
        operations = set()
        self.do_unreserve(cr, uid, [x.id for x in self.browse(cr, uid, ids, context=context) if x.reserved_quant_ids and x.state in ['confirmed', 'waiting', 'assigned']], context=context)
        for move in self.browse(cr, uid, ids, context=context):
            if move.state not in ('confirmed', 'waiting', 'assigned'):
                continue
            if move.location_id.usage in ('supplier', 'inventory', 'production'):
                to_assign_moves.add(move.id)
                #in case the move is returned, we want to try to find quants before forcing the assignment
                if not move.origin_returned_move_id:
                    continue
            if move.product_id.type == 'consu':
                to_assign_moves.add(move.id)
                continue
            else:
                todo_moves.append(move)

                #we always search for yet unassigned quants
                main_domain[move.id] = [('reservation_id', '=', False), ('qty', '>', 0)]

                #if the move is preceeded, restrict the choice of quants in the ones moved previously in original move
                ancestors = self.find_move_ancestors(cr, uid, move, context=context)
                if move.state == 'waiting' and not ancestors:
                    #if the waiting move hasn't yet any ancestor (PO/MO not confirmed yet), don't find any quant available in stock
                    main_domain[move.id] += [('id', '=', False)]
                elif ancestors:
                    main_domain[move.id] += [('history_ids', 'in', ancestors)]

                #if the move is returned from another, restrict the choice of quants to the ones that follow the returned move
                if move.origin_returned_move_id:
                    main_domain[move.id] += [('history_ids', 'in', move.origin_returned_move_id.id)]
                for link in move.linked_move_operation_ids:
                    operations.add(link.operation_id)
        # Check all ops and sort them: we want to process first the packages, then operations with lot then the rest
        operations = list(operations)
        operations.sort(key=lambda x: ((x.package_id and not x.product_id) and -4 or 0) + (x.package_id and -2 or 0) + (x.pack_lot_ids and -1 or 0))
        for ops in operations:
            #first try to find quants based on specific domains given by linked operations for the case where we want to rereserve according to existing pack operations
            if not (ops.product_id and ops.pack_lot_ids):
                for record in ops.linked_move_operation_ids:
                    move = record.move_id
                    if move.id in main_domain:
                        qty = record.qty
                        domain = main_domain[move.id]
                        if qty:
                            quants = quant_obj.quants_get_preferred_domain(cr, uid, qty, move, ops=ops, domain=domain, preferred_domain_list=[], context=context)
                            quant_obj.quants_reserve(cr, uid, quants, move, record, context=context)
            else:
                lot_qty = {}
                rounding = ops.product_id.uom_id.rounding
                for pack_lot in ops.pack_lot_ids:
                    lot_qty[pack_lot.lot_id.id] = uom_obj._compute_qty(cr, uid, ops.product_uom_id.id, pack_lot.qty, ops.product_id.uom_id.id)
                for record in ops.linked_move_operation_ids.filtered(lambda x: x.move_id.id in main_domain):
                    move_qty = record.qty
                    move = record.move_id
                    domain = main_domain[move.id]
                    for lot in lot_qty:
                        if float_compare(lot_qty[lot], 0, precision_rounding=rounding) > 0 and float_compare(move_qty, 0, precision_rounding=rounding) > 0:
                            qty = min(lot_qty[lot], move_qty)
                            quants = quant_obj.quants_get_preferred_domain(cr, uid, qty, move, ops=ops, lot_id=lot, domain=domain, preferred_domain_list=[], context=context)
                            quant_obj.quants_reserve(cr, uid, quants, move, record, context=context)
                            lot_qty[lot] -= qty
                            move_qty -= qty

        for move in todo_moves:
            #then if the move isn't totally assigned, try to find quants without any specific domain
            if (move.state != 'assigned') and not context.get("reserve_only_ops"):
                qty_already_assigned = move.reserved_availability
                qty = move.product_qty - qty_already_assigned
                quants = quant_obj.quants_get_preferred_domain(cr, uid, qty, move, domain=main_domain[move.id], preferred_domain_list=[], context=context)
                quant_obj.quants_reserve(cr, uid, quants, move, context=context)

        #force assignation of consumable products and incoming from supplier/inventory/production
        # Do not take force_assign as it would create pack operations
        if to_assign_moves:
            self.write(cr, uid, list(to_assign_moves), {'state': 'assigned'}, context=context)
        if not no_prepare:
            self.check_recompute_pack_op(cr, uid, ids, context=context)

    def _move_quants_by_lot(self, cr, uid, ops, lot_qty, quants_taken, false_quants, lot_move_qty, quant_dest_package_id, context=None):
        """
        This function is used to process all the pack operation lots of a pack operation
        For every move:
            First, we check the quants with lot already reserved (and those are already subtracted from the lots to do)
            Then go through all the lots to process:
                Add reserved false lots lot by lot
                Check if there are not reserved quants or reserved elsewhere with that lot or without lot (with the traditional method)
        """
        quant_obj = self.pool['stock.quant']
        fallback_domain = [('reservation_id', '=', False)]
        fallback_domain2 = ['&', ('reservation_id', 'not in', [x for x in lot_move_qty.keys()]), ('reservation_id', '!=', False)]
        preferred_domain_list = [fallback_domain] + [fallback_domain2]
        rounding = ops.product_id.uom_id.rounding
        for move in lot_move_qty:
            move_quants_dict = {}
            move_rec = self.pool['stock.move'].browse(cr, uid, move, context=context)
            # Assign quants already reserved with lot to the correct
            for quant in quants_taken:
                if quant[0] <= move_rec.reserved_quant_ids:
                    move_quants_dict.setdefault(quant[0].lot_id.id, [])
                    move_quants_dict[quant[0].lot_id.id] += [quant]
            false_quants_move = [x for x in false_quants if x[0].reservation_id.id == move]
            for lot in lot_qty:
                move_quants_dict.setdefault(lot, [])
                redo_false_quants = False
                # Take remaining reserved quants with  no lot first
                # (This will be used mainly when incoming had no lot and you do outgoing with)
                while false_quants_move and float_compare(lot_qty[lot], 0, precision_rounding=rounding) > 0 and float_compare(lot_move_qty[move], 0, precision_rounding=rounding) > 0:
                    qty_min = min(lot_qty[lot], lot_move_qty[move])
                    if false_quants_move[0].qty > qty_min:
                        move_quants_dict[lot] += [(false_quants_move[0], qty_min)]
                        qty = qty_min
                        redo_false_quants = True
                    else:
                        qty = false_quants_move[0].qty
                        move_quants_dict[lot] += [(false_quants_move[0], qty)]
                        false_quants_move.pop(0)
                    lot_qty[lot] -= qty
                    lot_move_qty[move] -= qty

                # Search other with first matching lots and then without lots
                if float_compare(lot_move_qty[move], 0, precision_rounding=rounding) > 0 and float_compare(lot_qty[lot], 0, precision_rounding=rounding) > 0:
                    # Search if we can find quants with that lot
                    domain = [('qty', '>', 0)]
                    qty = min(lot_qty[lot], lot_move_qty[move])
                    quants = quant_obj.quants_get_preferred_domain(cr, uid, qty, move_rec, ops=ops, lot_id=lot, domain=domain,
                                                        preferred_domain_list=preferred_domain_list, context=context)
                    move_quants_dict[lot] += quants
                    lot_qty[lot] -= qty
                    lot_move_qty[move] -= qty

                #Move all the quants related to that lot/move
                if move_quants_dict[lot]:
                    quant_obj.quants_move(cr, uid, move_quants_dict[lot], move_rec, ops.location_dest_id, location_from=ops.location_id,
                                                    lot_id=lot, owner_id=ops.owner_id.id, src_package_id=ops.package_id.id,
                                                    dest_package_id=quant_dest_package_id, context=context)
                    if redo_false_quants:
                        move_rec = self.pool['stock.move'].browse(cr, uid, move, context=context)
                        false_quants_move = [x for x in move_rec.reserved_quant_ids if (not x.lot_id) and (x.owner_id.id == ops.owner_id.id) \
                                             and (x.location_id.id == ops.location_id.id) and (x.package_id.id != ops.package_id.id)]

    def action_done(self, cr, uid, ids, context=None):
        """ Process completely the moves given as ids and if all moves are done, it will finish the picking.
        """
        context = context or {}
        picking_obj = self.pool.get("stock.picking")
        quant_obj = self.pool.get("stock.quant")
        uom_obj = self.pool.get("product.uom")
        todo = [move.id for move in self.browse(cr, uid, ids, context=context) if move.state == "draft"]
        if todo:
            ids = self.action_confirm(cr, uid, todo, context=context)
        pickings = set()
        procurement_ids = set()
        #Search operations that are linked to the moves
        operations = set()
        move_qty = {}
        for move in self.browse(cr, uid, ids, context=context):
            if move.picking_id:
                pickings.add(move.picking_id.id)
            move_qty[move.id] = move.product_qty
            for link in move.linked_move_operation_ids:
                operations.add(link.operation_id)

        #Sort operations according to entire packages first, then package + lot, package only, lot only
        operations = list(operations)
        operations.sort(key=lambda x: ((x.package_id and not x.product_id) and -4 or 0) + (x.package_id and -2 or 0) + (x.pack_lot_ids and -1 or 0))

        for ops in operations:
            if ops.picking_id:
                pickings.add(ops.picking_id.id)
            entire_pack=False
            if ops.product_id:
                #If a product is given, the result is always put immediately in the result package (if it is False, they are without package)
                quant_dest_package_id  = ops.result_package_id.id
            else:
                # When a pack is moved entirely, the quants should not be written anything for the destination package
                quant_dest_package_id = False
                entire_pack=True
            lot_qty = {}
            tot_qty = 0.0
            for pack_lot in ops.pack_lot_ids:
                qty = uom_obj._compute_qty(cr, uid, ops.product_uom_id.id, pack_lot.qty, ops.product_id.uom_id.id)
                lot_qty[pack_lot.lot_id.id] = qty
                tot_qty += pack_lot.qty
            if ops.pack_lot_ids and ops.product_id and float_compare(tot_qty, ops.product_qty, precision_rounding=ops.product_uom_id.rounding) != 0.0:
                raise UserError(_('You have a difference between the quantity on the operation and the quantities specified for the lots. '))

            quants_taken = []
            false_quants = []
            lot_move_qty = {}
            #Group links by move first
            move_qty_ops = {}
            for record in ops.linked_move_operation_ids:
                move = record.move_id
                if not move_qty_ops.get(move):
                    move_qty_ops[move] = record.qty
                else:
                    move_qty_ops[move] += record.qty
            #Process every move only once for every pack operation
            for move in move_qty_ops:
                main_domain = [('qty', '>', 0)]
                self.check_tracking(cr, uid, move, ops, context=context)
                preferred_domain = [('reservation_id', '=', move.id)]
                fallback_domain = [('reservation_id', '=', False)]
                fallback_domain2 = ['&', ('reservation_id', '!=', move.id), ('reservation_id', '!=', False)]
                if not ops.pack_lot_ids:
                    preferred_domain_list = [preferred_domain] + [fallback_domain] + [fallback_domain2]
                    quants = quant_obj.quants_get_preferred_domain(cr, uid, move_qty_ops[move], move, ops=ops, domain=main_domain,
                                                        preferred_domain_list=preferred_domain_list, context=context)
                    quant_obj.quants_move(cr, uid, quants, move, ops.location_dest_id, location_from=ops.location_id,
                                          lot_id=False, owner_id=ops.owner_id.id, src_package_id=ops.package_id.id,
                                          dest_package_id=quant_dest_package_id, entire_pack=entire_pack, context=context)
                else:
                    # Check what you can do with reserved quants already
                    qty_on_link = move_qty_ops[move]
                    rounding = ops.product_id.uom_id.rounding
                    for reserved_quant in move.reserved_quant_ids:
                        if (reserved_quant.owner_id.id != ops.owner_id.id) or (reserved_quant.location_id.id != ops.location_id.id) or \
                                (reserved_quant.package_id.id != ops.package_id.id):
                            continue
                        if not reserved_quant.lot_id:
                            false_quants += [reserved_quant]
                        elif float_compare(lot_qty.get(reserved_quant.lot_id.id, 0), 0, precision_rounding=rounding) > 0:
                            if float_compare(lot_qty[reserved_quant.lot_id.id], reserved_quant.qty, precision_rounding=rounding) >= 0:
                                lot_qty[reserved_quant.lot_id.id] -= reserved_quant.qty
                                quants_taken += [(reserved_quant, reserved_quant.qty)]
                                qty_on_link -= reserved_quant.qty
                            else:
                                quants_taken += [(reserved_quant, lot_qty[reserved_quant.lot_id.id])]
                                lot_qty[reserved_quant.lot_id.id] = 0
                                qty_on_link -= lot_qty[reserved_quant.lot_id.id]
                    lot_move_qty[move.id] = qty_on_link

                if not move_qty.get(move.id):
                    raise UserError(_("The roundings of your Unit of Measures %s on the move vs. %s on the product don't allow to do these operations or you are not transferring the picking at once. ") % (move.product_uom.name, move.product_id.uom_id.name))
                move_qty[move.id] -= move_qty_ops[move]

            #Handle lots separately
            if ops.pack_lot_ids:
                self._move_quants_by_lot(cr, uid, ops, lot_qty, quants_taken, false_quants, lot_move_qty, quant_dest_package_id, context=context)

            # Handle pack in pack
            if not ops.product_id and ops.package_id and ops.result_package_id.id != ops.package_id.parent_id.id:
                self.pool.get('stock.quant.package').write(cr, SUPERUSER_ID, [ops.package_id.id], {'parent_id': ops.result_package_id.id}, context=context)
        #Check for remaining qtys and unreserve/check move_dest_id in
        move_dest_ids = set()
        for move in self.browse(cr, uid, ids, context=context):
            move_qty_cmp = float_compare(move_qty[move.id], 0, precision_rounding=move.product_id.uom_id.rounding)
            if move_qty_cmp > 0:  # (=In case no pack operations in picking)
                main_domain = [('qty', '>', 0)]
                preferred_domain = [('reservation_id', '=', move.id)]
                fallback_domain = [('reservation_id', '=', False)]
                fallback_domain2 = ['&', ('reservation_id', '!=', move.id), ('reservation_id', '!=', False)]
                preferred_domain_list = [preferred_domain] + [fallback_domain] + [fallback_domain2]
                self.check_tracking(cr, uid, move, False, context=context)
                qty = move_qty[move.id]
                quants = quant_obj.quants_get_preferred_domain(cr, uid, qty, move, domain=main_domain, preferred_domain_list=preferred_domain_list, context=context)
                quant_obj.quants_move(cr, uid, quants, move, move.location_dest_id, lot_id=move.restrict_lot_id.id, owner_id=move.restrict_partner_id.id, context=context)

            # If the move has a destination, add it to the list to reserve
            if move.move_dest_id and move.move_dest_id.state in ('waiting', 'confirmed'):
                move_dest_ids.add(move.move_dest_id.id)

            if move.procurement_id:
                procurement_ids.add(move.procurement_id.id)

            #unreserve the quants and make them available for other operations/moves
            quant_obj.quants_unreserve(cr, uid, move, context=context)
        # Check the packages have been placed in the correct locations
        self._check_package_from_moves(cr, uid, ids, context=context)
        #set the move as done
        self.write(cr, uid, ids, {'state': 'done', 'date': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)}, context=context)
        self.pool.get('procurement.order').check(cr, uid, list(procurement_ids), context=context)
        #assign destination moves
        if move_dest_ids:
            self.action_assign(cr, uid, list(move_dest_ids), context=context)
        #check picking state to set the date_done is needed
        done_picking = []
        for picking in picking_obj.browse(cr, uid, list(pickings), context=context):
            if picking.state == 'done' and not picking.date_done:
                done_picking.append(picking.id)
        if done_picking:
            picking_obj.write(cr, uid, done_picking, {'date_done': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)}, context=context)
        return True

    def action_scrap(self, cr, uid, ids, quantity, location_id, restrict_lot_id=False, restrict_partner_id=False, context=None):
        """ Move the scrap/damaged product into scrap location
        @param cr: the database cursor
        @param uid: the user id
        @param ids: ids of stock move object to be scrapped
        @param quantity : specify scrap qty
        @param location_id : specify scrap location
        @param context: context arguments
        @return: Scraped lines
        """
        quant_obj = self.pool.get("stock.quant")
        #quantity should be given in MOVE UOM
        if quantity <= 0:
            raise UserError(_('Please provide a positive quantity to scrap.'))
        res = []
        for move in self.browse(cr, uid, ids, context=context):
            source_location = move.location_id
            if move.state == 'done':
                source_location = move.location_dest_id
            #Previously used to prevent scraping from virtual location but not necessary anymore
            #if source_location.usage != 'internal':
                #restrict to scrap from a virtual location because it's meaningless and it may introduce errors in stock ('creating' new products from nowhere)
                #raise UserError(_('Forbidden operation: it is not allowed to scrap products from a virtual location.'))
            move_qty = move.product_qty
            default_val = {
                'location_id': source_location.id,
                'product_uom_qty': quantity,
                'state': move.state,
                'scrapped': True,
                'location_dest_id': location_id,
                'restrict_lot_id': restrict_lot_id,
                'restrict_partner_id': restrict_partner_id,
            }
            new_move = self.copy(cr, uid, move.id, default_val)

            res += [new_move]
            product_obj = self.pool.get('product.product')
            for product in product_obj.browse(cr, uid, [move.product_id.id], context=context):
                if move.picking_id:
                    uom = product.uom_id.name if product.uom_id else ''
                    message = _("%s %s %s has been <b>moved to</b> scrap.") % (quantity, uom, product.name)
                    move.picking_id.message_post(body=message)

            # We "flag" the quant from which we want to scrap the products. To do so:
            #    - we select the quants related to the move we scrap from
            #    - we reserve the quants with the scrapped move
            # See self.action_done, et particularly how is defined the "preferred_domain" for clarification
            scrap_move = self.browse(cr, uid, new_move, context=context)
            if move.state == 'done' and scrap_move.location_id.usage not in ('supplier', 'inventory', 'production'):
                domain = [('qty', '>', 0), ('history_ids', 'in', [move.id])]
                # We use scrap_move data since a reservation makes sense for a move not already done
                quants = quant_obj.quants_get_preferred_domain(cr, uid, quantity, scrap_move, domain=domain, context=context)
                quant_obj.quants_reserve(cr, uid, quants, scrap_move, context=context)
        self.action_done(cr, uid, res, context=context)
        return res

    def split(self, cr, uid, move, qty, restrict_lot_id=False, restrict_partner_id=False, context=None):
        """ Splits qty from move move into a new move
        :param move: browse record
        :param qty: float. quantity to split (given in product UoM)
        :param restrict_lot_id: optional production lot that can be given in order to force the new move to restrict its choice of quants to this lot.
        :param restrict_partner_id: optional partner that can be given in order to force the new move to restrict its choice of quants to the ones belonging to this partner.
        :param context: dictionay. can contains the special key 'source_location_id' in order to force the source location when copying the move

        returns the ID of the backorder move created
        """
        if move.state in ('done', 'cancel'):
            raise UserError(_('You cannot split a move done'))
        if move.state == 'draft':
            #we restrict the split of a draft move because if not confirmed yet, it may be replaced by several other moves in
            #case of phantom bom (with mrp module). And we don't want to deal with this complexity by copying the product that will explode.
            raise UserError(_('You cannot split a draft move. It needs to be confirmed first.'))

        if move.product_qty <= qty or qty == 0:
            return move.id

        uom_obj = self.pool.get('product.uom')
        context = context or {}

        #HALF-UP rounding as only rounding errors will be because of propagation of error from default UoM
        uom_qty = uom_obj._compute_qty_obj(cr, uid, move.product_id.uom_id, qty, move.product_uom, rounding_method='HALF-UP', context=context)
        defaults = {
            'product_uom_qty': uom_qty,
            'procure_method': 'make_to_stock',
            'restrict_lot_id': restrict_lot_id,
            'split_from': move.id,
            'procurement_id': move.procurement_id.id,
            'move_dest_id': move.move_dest_id.id,
            'origin_returned_move_id': move.origin_returned_move_id.id,
        }

        if restrict_partner_id:
            defaults['restrict_partner_id'] = restrict_partner_id

        if context.get('source_location_id'):
            defaults['location_id'] = context['source_location_id']
        new_move = self.copy(cr, uid, move.id, defaults, context=context)

        ctx = context.copy()
        ctx['do_not_propagate'] = True
        self.write(cr, uid, [move.id], {
            'product_uom_qty': move.product_uom_qty - uom_qty,
        }, context=ctx)

        if move.move_dest_id and move.propagate and move.move_dest_id.state not in ('done', 'cancel'):
            new_move_prop = self.split(cr, uid, move.move_dest_id, qty, context=context)
            self.write(cr, uid, [new_move], {'move_dest_id': new_move_prop}, context=context)
        #returning the first element of list returned by action_confirm is ok because we checked it wouldn't be exploded (and
        #thus the result of action_confirm should always be a list of 1 element length)
        return self.action_confirm(cr, uid, [new_move], context=context)[0]