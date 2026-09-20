def generate_names(length, amount=40):
    # Generate Discord-style usernames with a dot and short mixed alphanumeric tail.
    # Examples: d.i1, a.7k, x.9m2
    alphabet = string.ascii_lowercase + string.digits
    candidates = set()
    while len(candidates) < amount:
        prefix = random.choice(string.ascii_lowercase)
        tail_len = max(1, length - 2)
        suffix = "".join(random.choice(alphabet) for _ in range(tail_len))
        value = f"{prefix}.{suffix}"
        if len(value) == length or len(value) == length + 1:
            candidates.add(value)
    return sorted(candidates)
