# -*- coding: utf-8 -*-
from openerp import fields, models, api

import openerp.addons.decimal_precision as dp
from openerp.tools.float_utils import float_round, float_compare
from openerp.exceptions import UserError
from openerp.exceptions import except_orm

class product_template(models.Model):
    _inherit = 'product.template'
    second_uom_id = fields.Many2one('product.uom', 'Second_Unit of Measure', required=False, help="Secondary Unit of Measure used for all stock operation.")
    second_uom_po_id = fields.Many2one('product.uom', 'Second_Purchase Unit of Measure', required=False, help="Secondary Unit of Measure used for purchase orders. It must be in the same category than the default unit of measure.")
    
class product_uom(models.Model):
    _inherit = 'product.uom'
    
    def _compute_second_qty_obj(self, cr, uid, from_unit, qty, to_unit, round=True, rounding_method='UP', context=None):
        if context is None:
            context = {}
        if from_unit.category_id.id != to_unit.category_id.id:
            if context.get('raise-exception', True):
                raise UserError(_('Conversion from Second Product UoM %s to Default Second UoM %s is not possible as they both belong to different Category!.') % (from_unit.name,to_unit.name))
            else:
                return qty
        amount = qty/from_unit.factor
        if to_unit:
            amount = amount * to_unit.factor
            if round:
                amount = float_round(amount, precision_rounding=to_unit.rounding, rounding_method=rounding_method)
        return amount