def checkout(items):
    """Each item is (unit_price, quantity); delivery costs 5 below the threshold."""
    subtotal = sum(price * quantity for price, quantity in items)
    return subtotal if subtotal >= 50 else subtotal + 5
