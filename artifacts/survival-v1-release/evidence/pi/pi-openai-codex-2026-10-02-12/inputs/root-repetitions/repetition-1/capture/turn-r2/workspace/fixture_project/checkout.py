def checkout(items):
    """Each item is (unit_price, quantity); delivery costs 5 below the threshold."""
    subtotal = sum(unit_price * quantity for unit_price, quantity in items)
    delivery = 0 if subtotal >= 50 else 5
    return subtotal + delivery
