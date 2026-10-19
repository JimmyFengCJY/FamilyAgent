from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal("1000000000"):
            raise ValueError("金额需在 0 到 10 亿之间")
        return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("请输入有效金额") from exc


def calculate_budget(data):
    income = money(data["income"])
    items = [{"name": i["name"], "amount": str(money(i["amount"]))} for i in data["items"]]
    expense = sum((Decimal(i["amount"]) for i in items), Decimal("0.00"))
    remaining = income - expense
    return {"income": str(income), "expenses": str(expense), "remaining": str(remaining),
            "savings_rate": str((remaining / income * 100).quantize(Decimal("0.1"))) if income else None,
            "items": items, "currency": data.get("currency", "CNY"),
            "source": "用户本次录入；Decimal 确定性计算，非市场报价"}
