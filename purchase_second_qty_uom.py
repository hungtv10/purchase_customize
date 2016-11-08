# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import datetime
from openerp import fields, models, api,  _, SUPERUSER_ID
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT

class Purchase_second_qty_uom(models.Model):
    _inherit = 'purchase.order.line'
     
    @api.depends('invoice_lines.invoice_id.state')
    def _compute_second_qty_invoiced(self):
        for line in self:
            qty = 0.0
            for inv_line in line.invoice_lines:
                if inv_line.invoice_id.state not in ['cancel']:
                    qty += inv_line.uom_id._compute_second_qty_obj(inv_line.uom_id, inv_line.quantity, line.product_uom)
            line.qty_invoiced = qty
            
    @api.depends('order_id.state', 'move_ids.state')
    def _compute_second_qty_received(self):
        for line in self:
            if line.order_id.state not in ['purchase', 'done']:
                line.second_qty_received = 0.0
                continue
            if line.product_id.type not in ['consu', 'product']:
                line.second_qty_received = line.second_qty
                continue
            bom_delivered = self.sudo()._get_bom_delivered(line.sudo())
            if bom_delivered and any(bom_delivered.values()):
                total = line.second_qty
            elif bom_delivered:
                total = 0.0
            else:
                total = 0.0
                for move in line.move_ids:
                    if move.state == 'done':
                        total += move.second_product_uom_qty
            line.second_qty_received = total
    
    second_qty = fields.Float("Second Qty", default=False)
    second_uom = fields.Many2one('product.uom',string="Second Purch. UOM")
    second_qty_invoiced = fields.Float(compute='_compute_second_qty_invoiced', string="Second Billed Qty", store=True)
    second_qty_received = fields.Float(compute='_compute_second_qty_received', string="Second Received Qty", store=True)
    
    @api.onchange('product_id')
    def onchange_product_id(self):
        result = {}
        if not self.product_id:
            return result

        # Reset date, price and quantity since _onchange_quantity will provide default values
        self.date_planned = datetime.today().strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        self.price_unit = self.product_qty = self.second_product_qty = 0.0
        self.product_uom = self.product_id.uom_po_id or self.product_id.uom_id
        self.second_uom = self.product_id.second_uom_po_id or self.product_id.second_uom_id
        result['domain'] = {'product_uom': [('category_id', '=', self.product_id.uom_id.category_id.id)]}

        product_lang = self.product_id.with_context({
            'lang': self.partner_id.lang,
            'partner_id': self.partner_id.id,
        })
        self.name = product_lang.display_name
        if product_lang.description_purchase:
            self.name += '\n' + product_lang.description_purchase

        fpos = self.order_id.fiscal_position_id
        if self.env.uid == SUPERUSER_ID:
            company_id = self.env.user.company_id.id
            self.taxes_id = fpos.map_tax(self.product_id.supplier_taxes_id.filtered(lambda r: r.company_id.id == company_id))
        else:
            self.taxes_id = fpos.map_tax(self.product_id.supplier_taxes_id)

        self._suggest_quantity()
        self._onchange_quantity()

        return result