# -*- coding: utf-8 -*-
from openerp import fields, models, api

class Purchase_second_qty_uom(models.Model):
    _inherit = 'purchase.order.line'
     
    @api.depends('invoice_lines.invoice_id.state')
    def _compute_second_qty_invoiced(self):
        for line in self:
            qty = 0.0
            for inv_line in line.invoice_lines:
                if inv_line.invoice_id.state not in ['cancel']:
                    qty += inv_line.uom_id._compute_qty_obj(inv_line.uom_id, inv_line.quantity, line.product_uom)
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
                        total += move.product_uom_qty
            line.second_qty_received = total
    
    second_qty = fields.Float("Second_Qty", default=False)
    second_uom = fields.Many2one('product.uom',string="Second_Purchase UOM")
    second_qty_invoiced = fields.Float(compute='_compute_second_qty_invoiced', string="Second_Billed Qty", store=True)
    second_qty_received = fields.Float(compute='_compute_second_qty_received', string="Second_Received Qty", store=True)


class Product_second_uom(models.Model):
    _inherit = 'product.template'