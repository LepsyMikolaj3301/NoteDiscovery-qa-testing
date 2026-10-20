def convert_to_polish_name(number: int) -> str:
    if number < 0 or number > 999999:
        raise ValueError("Number out of supported range <0, 999999>")

    units = [
        "zero", "jeden", "dwa", "trzy", "cztery",
        "pięć", "sześć", "siedem", "osiem", "dziewięć"
    ]
    teens = [
        "dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście",
        "piętnaście", "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście"
    ]
    tens = [
        "", "dziesięć", "dwadzieścia", "trzydzieści", "czterdzieści",
        "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt",
        "osiemdziesiąt", "dziewięćdziesiąt"
    ]
    hundreds = [
        "", "sto", "dwieście", "trzysta", "czterysta",
        "pięćset", "sześćset", "siedemset", "osiemset", "dziewięćset"
    ]

    def three_digits_to_words(n):
        if n == 0:
            return ""

        result = []
        h = n // 100
        t = (n % 100) // 10
        u = n % 10

        if h > 0:
            result.append(hundreds[h])

        if t == 1:  
            result.append(teens[u])
        else:
            if t > 0:
                result.append(tens[t])
            if u > 0:
                result.append(units[u])

        return " ".join([r for r in result if r])

    if number == 0:
        return "zero"

    thousands = number // 1000
    remainder = number % 1000

    words = []

    if thousands > 0:
        if thousands == 1:
            words.append("jeden tysiąc")
        elif thousands % 10 in [2, 3, 4] and not (12 <= thousands % 100 <= 14):
            words.append(three_digits_to_words(thousands) + " tysiące")
        else:
            words.append(three_digits_to_words(thousands) + " tysięcy")

    if remainder > 0:
        words.append(three_digits_to_words(remainder))

    return " ".join(words).strip()