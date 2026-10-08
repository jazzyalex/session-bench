def checkout(items):
    """Each item is (unit_price, quantity); delivery is free when subtotal is at least 50."""
    subtotal = sum(price * quantity for price, quantity in items)
    delivery = 0 if subtotal >= 50 else 5
    return subtotal + delivery
