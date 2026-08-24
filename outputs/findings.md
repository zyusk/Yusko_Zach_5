# Findings — SAUS Railroad Mileage Digitization (Assignment 5)



In this project I had to learn the hard way that OCR can fully misread a digit, and the AI will back it up by saying this number is true, and try to convince me of it. I was skeptical since it was only able to scrape 3/48 years of data, so trusting those 3 seemed a little to optimistic. After manually looking at the pdfs, I draw out all of the real numbers and made a manual table, after that I had to train OCR to match those numbers to the files it reads and make sure it is correct. This was the only way to produce a reproducible code since trusting the OCR completely was out of the cards. 

This concept came up multiple times as it misread not just numbers, but also categories. For example, it read the 1917 and 1918 years class for Class-I-railways-only subset instead of the full network figure.
 1949 and 1950 stayed out of my final panel. Both years' only automated
extraction turned out to be real numbers mislabeled with the wrongyear, rather than guess which was right, I left them out and documented exactly why.






