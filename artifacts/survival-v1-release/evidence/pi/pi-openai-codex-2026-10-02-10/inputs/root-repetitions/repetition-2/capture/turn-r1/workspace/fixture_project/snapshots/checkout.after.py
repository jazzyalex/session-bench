def checkout(items):
    """Each item is (unit_price, quantity); delivery costs 5 below the threshold."""
    subtotal = sum(price * quantity for price, quantity in items)
    return subtotal + (0 if subtotal >= 50 else 5)
