import math


def coord_2d_to_1d(col, row, items):
    """
    Converts 2D array coordinates into 1D coordinate
    Args:
        col:
        row:
        items: number of items (size of single dimension)
    """
    return col + row * items


def calc_distance(doc1, doc2):
    """
    Cosine Similarity (d1, d2) =  Dot product(d1, d2) / ||d1|| * ||d2||
    Dot product (d1,d2) = d1[0] * d2[0] + d1[1] * d2[1] * ... * d1[n] * d2[n]
    ||d1|| = square root(d1[0]2 + d1[1]2 + ... + d1[n]2)
    ||d2|| = square root(d2[0]2 + d2[1]2 + ... + d2[n]2)
    """
    if doc1.id == doc2.id:
        return 1.0

    dot_product = 0.0
    d1 = 0.0
    d2 = 0.0

    for token_1 in doc1.tfidf:
        tfidf_1 = doc1.tfidf[token_1]
        tfidf_2 = 0.0
        try:
            tfidf_2 = doc2.tfidf[token_1]
        except:
            tfidf_2 = 0.0
        dot_product += tfidf_2 * tfidf_1

        d1 += math.pow(tfidf_1, 2)
    d1 = math.sqrt(d1)

    for token_2 in doc2.tfidf:
        tfidf_2 = doc2.tfidf[token_2]
        d2 += math.pow(tfidf_2, 2)
    d2 = math.sqrt(d2)
    return int(dot_product / (d1 * d2) * 1000) / 1000.0


def str_1d_as_2d(arr, size):
    s = ''

    line = '{:>8}'.format('')
    for col in range(size):
        line += '{:>8}'.format(col)
    s += line + '\n'

    for row in range(size):
        line = '{:>8}'.format(row)
        for col in range(size):
            line += '{:>8}'.format(arr[coord_2d_to_1d(col, row, size)])
        s += line + '\n'
    return s
