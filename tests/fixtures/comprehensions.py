numbers = [1, 2, 3, 4, 5]
squares = [x * x for x in numbers]
evens = {x for x in numbers if x % 2 == 0}
mapping = {x: x * 2 for x in numbers}
gen = (x + 1 for x in numbers)
