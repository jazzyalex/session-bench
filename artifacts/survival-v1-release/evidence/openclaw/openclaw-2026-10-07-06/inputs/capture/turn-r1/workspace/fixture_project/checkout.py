def checkout(items):
    """Each item is (unit_price, quantity); delivery costs 5 below the threshold."""
    # Deliberate R1 defect: quantity is ignored and delivery is always charged.
    subtotal = sum(price for price, quantity in items)
    return subtotal + 5
