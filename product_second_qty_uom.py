# -*- coding: utf-8 -*-
from openerp import fields, models, api

class product_template(models.Model):
    _inherit = 'product.template'
    second_uom_id = fields.Many2one('product.uom', 'Second_Unit of Measure', required=False, help="Secondary Unit of Measure used for all stock operation.")
    second_uom_po_id = fields.Many2one('product.uom', 'Second_Purchase Unit of Measure', required=False, help="Secondary Unit of Measure used for purchase orders. It must be in the same category than the default unit of measure.")