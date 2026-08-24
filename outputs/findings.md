# Findings — SAUS Railroad Mileage Digitization (Assignment 5)



In this project I had to learn the hard way that OCR can fully misread a digit, and the AI will back it up by saying the number is true. Early on, one batch only correctly read 3 of 48 years, so I didn't trust it. Even after I fixed the main blocker and extraction got most years right (39 of 48 matched my manually verified numbers), 9 of the rest were still silently wrong, off by anywhere from a few miles to 50,000, and none of those 9 tripped the code's own plausibility check. Only checking every row by hand against the source PDF caught them. That's the real lesson: ~80% right isn't good enough when the code can't tell which part is wrong. So I drew the real numbers from the PDFs myself and used that manual table as ground truth for the panel and for validating the OCR, trusting it completely was never on the table.

This came up with categories too, for example it read the 1917/1918 Class-I-railways-only subset instead of the full network figure.

1949 and 1950 stayed out of my final panel: both years' only automated extraction turned out to be real numbers mislabeled with the wrong year, so rather than guess which was right, I left them out and documented why.






