# ruff: noqa: E501, RUF001  (data: one item per line; Chinese text uses full-width commas)
"""SYNTHETIC evaluation corpus for retrieval quality (design §11 P4 acceptance: recall@8).

Everything here is invented for testing: "Nimbus Outfitters" is a fictional outdoor-gear shop,
and every policy, number and person is made up. No customer data. Mixed Chinese and English on
purpose (the native provider's multilingual embedding and jieba full text are both exercised).

Keys name the items; a query lists the keys a good retrieval must return within its top 8.
"""

# Knowledge entries: (key, title, content)
ENTRIES: list[tuple[str, str, str]] = [
    (
        "refund-time",
        "Refund processing time",
        "Refunds are issued within 5 business days after the returned item reaches our warehouse.",
    ),
    (
        "refund-method",
        "Refund method",
        "Refunds go back to the original payment method; store credit only if the customer asks for it.",
    ),
    (
        "return-window",
        "Return window",
        "Unused items can be returned within 30 days of delivery with the original tags attached.",
    ),
    (
        "return-shipping",
        "Return shipping cost",
        "Return shipping is free for defective items; for change-of-mind returns a 6 EUR label fee is deducted.",
    ),
    (
        "exchange",
        "Exchanges",
        "We do not swap items directly: return the item for a refund and place a new order.",
    ),
    (
        "warranty-tents",
        "Tent warranty",
        "Tents carry a 2-year warranty against seam and zipper defects; wind damage is not covered.",
    ),
    (
        "warranty-boots",
        "Boot warranty",
        "Hiking boots have a 1-year warranty on soles coming loose.",
    ),
    (
        "shipping-eu",
        "Shipping within the EU",
        "Standard EU delivery takes 3 to 5 working days and is free above 60 EUR.",
    ),
    (
        "shipping-express",
        "Express shipping",
        "Express delivery arrives the next working day if ordered before 14:00 CET, for 12 EUR.",
    ),
    (
        "shipping-intl",
        "International shipping",
        "Outside the EU we ship to the UK, Switzerland and Norway only; customs duties are paid by the customer.",
    ),
    (
        "order-change",
        "Changing an order",
        "An order can be changed or cancelled until it is packed, usually within 2 hours of ordering.",
    ),
    (
        "order-tracking",
        "Tracking an order",
        "The tracking link is emailed when the parcel leaves the warehouse.",
    ),
    (
        "payment-methods",
        "Payment methods",
        "We accept cards, PayPal and bank transfer; bank transfers ship after the payment arrives.",
    ),
    (
        "invoice",
        "Invoices",
        "A VAT invoice is attached to the shipping confirmation email and can be downloaded from the account page.",
    ),
    (
        "size-guide",
        "Sizing",
        "Our boots run half a size small: order half a size up from your usual shoe size.",
    ),
    (
        "care-down",
        "Down jacket care",
        "Wash down jackets at 30 degrees with down detergent and tumble dry low with dryer balls.",
    ),
    (
        "repair",
        "Repair service",
        "We repair zippers and small tears for free in the first year; later repairs are quoted first.",
    ),
    (
        "loyalty",
        "Loyalty points",
        "Each euro spent earns one point; 100 points give a 5 EUR voucher.",
    ),
    (
        "student",
        "Student discount",
        "Students get 10% off with a verified student email, not combinable with other codes.",
    ),
    ("price-match", "Price match", "We do not match competitors' prices."),
    (
        "store-hours",
        "Store opening hours",
        "The Lyon store is open Tuesday to Saturday, 10:00 to 19:00.",
    ),
    ("zh-refund", "退款到账时间", "退货商品到达仓库后，退款会在五个工作日内原路退回。"),
    ("zh-invoice", "发票", "增值税发票随发货确认邮件发送，也可以在账户页面下载。"),
    ("zh-size", "尺码建议", "我们的登山鞋偏小半码，建议比平时的鞋码大半码。"),
    ("zh-express", "加急配送", "工作日下午两点前下单，次日送达，运费12欧元。"),
    ("zh-warranty", "帐篷保修", "帐篷的缝线和拉链问题享有两年保修，大风造成的损坏不在保修范围内。"),
    (
        "gift-card",
        "Gift cards",
        "Gift cards are valid for 3 years and can be used online and in store.",
    ),
    (
        "rental",
        "Gear rental",
        "Tents and sleeping bags can be rented in the Lyon store for weekend trips.",
    ),
]

# Documents: (key, file name, text). Paragraphs are their chunks in the native provider.
DOCUMENTS: list[tuple[str, str, str]] = [
    (
        "doc-camping",
        "camping-checklist.md",
        "# Winter camping checklist\n\n"
        "Bring a sleeping bag rated at least 5 degrees below the coldest expected night.\n\n"
        "Keep fuel canisters inside the sleeping bag overnight so the stove lights in the morning.\n\n"
        "Pitch the tent with the door away from the wind and dig the pegs into packed snow.\n",
    ),
    (
        "doc-zh-care",
        "保养指南.md",
        "# 装备保养指南\n\n"
        "冲锋衣每季清洗一次，洗后低温烘干可以恢复防水涂层。\n\n"
        "登山鞋用完后取出鞋垫自然晾干，不要放在暖气上烘烤。\n",
    ),
]

# One customer's memories (user "u1"): (key, content)
MEMORIES: list[tuple[str, str]] = [
    ("m-size", "The customer wears EU size 43 hiking boots."),
    ("m-order", "Order NB-1042 (a 2-person tent) was delivered on 3 November."),
    ("m-return", "The customer started a return for the 2-person tent because a zipper is broken."),
    ("m-lang", "The customer prefers answers in French."),
    ("m-lyon", "The customer lives near Lyon and sometimes visits the store."),
    ("m-student", "The customer is a student at the University of Lyon."),
    ("m-allergy", "用户对羊毛过敏，不买羊毛袜。"),
    ("m-trip", "用户计划十二月去阿尔卑斯山冬季露营。"),
]

# (query, expected keys): paraphrases, cross-language, memory + knowledge together
QUERIES: list[tuple[str, list[str]]] = [
    ("how long until I get my money back after returning something", ["refund-time"]),
    ("will the refund go to my credit card", ["refund-method"]),
    ("can I send back a jacket I bought three weeks ago", ["return-window"]),
    ("do I pay for the return label", ["return-shipping"]),
    ("I want a different size instead, can you swap it", ["exchange"]),
    ("the zipper on my tent broke, is that covered", ["warranty-tents", "m-return"]),
    ("my boot sole is peeling off", ["warranty-boots"]),
    ("when will my parcel arrive in Germany", ["shipping-eu"]),
    ("I need it tomorrow", ["shipping-express"]),
    ("do you deliver to Canada", ["shipping-intl"]),
    ("can I still cancel what I ordered an hour ago", ["order-change"]),
    ("where is my package", ["order-tracking"]),
    ("I need a VAT receipt for my company", ["invoice"]),
    ("what boot size should I buy", ["size-guide", "m-size"]),
    ("how do I wash a puffer jacket", ["care-down"]),
    ("I'm a student, do I get a discount", ["student", "m-student"]),
    ("退款多久能到账", ["zh-refund"]),
    ("我需要发票", ["zh-invoice"]),
    ("鞋子尺码怎么选", ["zh-size"]),
    ("how cold a sleeping bag do I need for winter camping", ["doc-camping"]),
    ("my stove won't light in the cold morning", ["doc-camping"]),
    ("冲锋衣怎么洗才能保持防水", ["doc-zh-care"]),
    ("can I borrow a tent for the weekend", ["rental"]),
    ("what did the customer order recently", ["m-order"]),
    ("which socks should we avoid recommending to this customer", ["m-allergy"]),
]
