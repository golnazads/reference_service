"""
Code to normalize and otherwise manipulate author lists.
"""

import regex as re
import editdistance
import unidecode

from flask import current_app

from referencesrv.resolver.common import Undecidable

# all author lists coming in need to be case-folded
# replaced van(?: der) with van|van der
SINGLE_NAME_RE = "(?:(?:d|de|de la|De|des|Des|in '[a-z]|van|van der|van den|van de|von|Mc|[A-Z]')[' ]?)?[A-Z][a-z]['A-Za-z]*"
LAST_NAME_PAT = re.compile(r"%s(?:[- ]%s)*" % (SINGLE_NAME_RE, SINGLE_NAME_RE))

ETAL = r"(([\s,]*and)?[\s,]*[Ee][Tt][.\s]*[Aa][Ll][.\s]+)?"
LAST_NAME_SUFFIX = r"([,\s]*[Jj][Rr][.,\s]+)?"

# This pattern should match author names with initials behind the last name
TRAILING_INIT_PAT = re.compile(r"(?P<last>%s%s)\s*,?\s+"
                               r"(?P<first>(?:[A-Z]\.[\s-]*)+)" % (LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX))
# This pattern should match author names with initals in front of the last name
LEADING_INIT_PAT = re.compile(r"(?P<first>(?:[A-Z]\.[\s-]*)+) "
                              r"(?P<last>%s%s)\s*,?" % (LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX))

# This pattern should match author names with first/middle name behind the last name
TRAILING_FULL_PAT = re.compile(r"(?P<last>%s%s)\s*,?\s+"
                               r"(?P<first>(?:[A-Z][A-Za-z]+\s*)(?:[A-Z][.\s])*)" % (LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX))
# This pattern should match author names with first/middle name in front of the last name
LEADING_FULL_PAT = re.compile(r"(?P<first>(?:[A-Z][A-Za-z]+\s*)(?:[A-Z][.\s])*) "
                              r"(?P<last>%s%s)\s*,?" % (LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX))

EXTRAS_PAT = re.compile(r"\b\s*(and|et\.? al\.?|jr)\b", re.I)

REF_GLUE_RE = r"[,;]?(\s*(?:and|&))?\s+"
NAMED_GROUP_PAT = re.compile(r"\?P<\w+>")

LEADING_INIT_AUTHORS_PAT = re.compile("%s(%s%s)*%s"%(
    NAMED_GROUP_PAT.sub("?:", LEADING_INIT_PAT.pattern), REF_GLUE_RE,
    NAMED_GROUP_PAT.sub("?:", LEADING_INIT_PAT.pattern), ETAL))

TRAILING_INIT_AUTHORS_PAT = re.compile("%s(%s%s)*%s"%(
    NAMED_GROUP_PAT.sub("?:", TRAILING_INIT_PAT.pattern), REF_GLUE_RE,
    NAMED_GROUP_PAT.sub("?:", TRAILING_INIT_PAT.pattern), ETAL))

COLLABORATION_PAT = re.compile(r"(?P<collaboration>[(\[]*[A-Za-z\s\-\/]+\s[Cc]ollaboration[s]?\s*[A-Z\.]*[\s.,)\]]+)")
COLLEAGUES_PAT = re.compile(r"(?P<andcolleagues>and\s\d+\s(co-authors|colleagues))")

SINGLE_WORD_EXTRACTOR = re.compile(r"\w+")

FIRST_CAPTIAL = re.compile(r"^([^A-Z0-9\"""]*[A-Z])")

REMOVE_AND = re.compile(r"(,?\s+and\s+)", re.IGNORECASE)
COMMA_BEFORE_AND = re.compile(r"(,)?(\s+and)", re.IGNORECASE)

CROP_IF_NEEDED = re.compile(r"((^(?!and|&).*(and|&)(?!,).*),)|((^(?!and|&).*(and|&)(?!,).*)$)|((^(?!,).*),)")

ETAL_HOOK = re.compile(r"(.* et\.? al\.?)\b", re.I)
AND_HOOK = re.compile(r"((?:[A-Z][.\s])?%s%s[,\s]+|%s%s[,\s]+(?:[A-Z][.\s])?)+(\b[Aa]nd|\s&)\s((?:[A-Z][.\s])?%s%s|%s%s(?:[A-Z][.\s])?)"
                      %(LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX, LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX,
                        LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX, LAST_NAME_PAT.pattern, LAST_NAME_SUFFIX))

def get_length_matched_authors(ref_string, matches):
    """
    make sure the author was matched from the beginning of the reference

    :param ref_string:
    :param matched:
    :return:
    """
    matched_str = ', '.join([' '.join(list(filter(None, author))).strip() for author in matches])
    count = 0
    for sub, full in zip(matched_str, ref_string):
        if sub != full:
            break
        count += 1
    return count

def get_author_pattern(ref_string):
    """
    returns a pattern matching authors in ref_string.

    The problem here is that initials may be leading or trailing.
    The function looks for patterns pointing on one or the other direction;
    if unsure, an Undecidable exception is raised.

    :param ref_string:
    :return:
    """
    # if there is a collaboration included in the list of authors
    # remove that to be able to decide if the author list is trailing or ending
    collaborators_idx, collaborators_len = get_collaborators(ref_string)

    patterns = [TRAILING_INIT_PAT, LEADING_INIT_PAT, TRAILING_FULL_PAT, LEADING_FULL_PAT]
    lengths = [0] * len(patterns)

    # if collaborator is listed before authors
    if collaborators_idx != 0:
        for i, pattern in enumerate(patterns):
            # lengths[i] = len(pattern.findall(ref_string[collaborators_len:]))
            lengths[i] = get_length_matched_authors(ref_string[collaborators_len:], pattern.findall(ref_string[collaborators_len:]))
    else:
        for i, pattern in enumerate(patterns):
            # lengths[i] = len(pattern.findall(ref_string))
            lengths[i] = get_length_matched_authors(ref_string, pattern.findall(ref_string))

    indices_max = [index for index, value in enumerate(lengths) if value == max(lengths)]
    if len(indices_max) != 1:
        indices_match = [index for index, value in enumerate(lengths) if value > 0]

        # if there were multiple max and one min, pick the min
        if len(indices_match) - len(indices_max) == 1:
            return patterns[min(indices_match)]

        # see which two or more patterns recognized this reference, turn the indices_max to on/off, convert to binary,
        # and then decimal, note that 1, 2, 4, and 8 do not get there
        on_off_value = int(''.join(['1' if i in indices_max else '0' for i in list(range(4))]),2)

        # all off, all on, or contradiction (ie, TRAILING on from one set of INIT or FULL with LEADING on from the other)
        if on_off_value in [0, 6, 9, 12, 15]:
            return None

        # 0011 pick fourth pattern
        # this happens when there is no init and last-first is not distinguishable with first-last,
        # so pick the latter one
        if on_off_value == 3:
            return patterns[3]
        # 0101 and 0111 pick second pattern
        if on_off_value in [5, 7]:
            return patterns[1]
        # 1010 and 1011 pick first pattern
        if on_off_value in [10, 11]:
            return patterns[0]
        # 1101 pick fourth pattern
        if on_off_value == 13:
            return patterns[3]
        # 1110 pick third pattern
        if on_off_value == 14:
            return patterns[2]

    return patterns[indices_max[0]]


def get_authors_recursive(ref_string, start_idx, author_pattern):
    """
    if there is a comma missing between the authors, or there is a out of place character,
    (ie, P. Bosetti, N. Brand t, M. Caleno, ...) RE gets confused,
    once substring is identified as author, continue on with the rest of ref_string
    to make sure all the authors are identified

    :param ref_string:
    :param start_idx:
    :param author_pattern:
    :return:
    """
    author_len = 0
    while True:
        if author_len > 0:
            first_capital = FIRST_CAPTIAL.match(ref_string[start_idx+author_len:])
            if first_capital:
                author_len += len(first_capital.group()) - 1
        author_match = author_pattern.match(ref_string[start_idx+author_len:])
        if author_match:
            author_len += len(author_match.group())
        else:
            break

    return author_len


def get_authors_last_attempt(ref_string):
    """
    last attempt to identify author(s)

    :param ref_string:
    :return:
    """
    # if there is an and, used that as an anchor
    match = AND_HOOK.match(ref_string)
    if match:
        return match.group(0).strip()
    # grab first author's lastname and include etal
    match = LAST_NAME_PAT.search(ref_string)
    if match:
        return match.group().strip()
    return None


def get_authors(ref_string):
    """
    returns something what should be the authors in ref_string, assuming
    the reference starts with them and they don't have spelled out first names.

    This works by returning the longest match of either leading or trailing
    authors starting at the beginning of ref_string.

    :param ref_string:
    :return:
    """
    if isinstance(ref_string, str):
        ref_string = unidecode.unidecode(ref_string)

    # if there are any collaborator(s) listed in the reference
    # remove them to be able to decide if the author list is trailing or ending
    # not if collaborators_idx is not zero, it means this is listed after author list
    # that would signal the end of author
    collaborators_idx, collaborators_len = get_collaborators(ref_string)
    # if there is a xxx colleagues or xxx co-authors
    # that would signal the end of author
    and_colleagues_idx, and_colleagues_len = get_and_colleagues(ref_string)

    if and_colleagues_len > 0:
        authors_len = and_colleagues_idx + and_colleagues_len
    elif collaborators_idx > 0:
        authors_len = collaborators_idx + collaborators_len
    else:
        # if there is et al, grab everything before it and do not bother with
        # deciding what format it is
        match = ETAL_HOOK.match(ref_string)
        if match:
            authors_len = len(match.group().strip())
        else:
            author_pattern = get_author_pattern(ref_string)
            if author_pattern:
                authors_len = get_authors_recursive(ref_string, collaborators_len, author_pattern)
            else:
                # if unable to parse author, assign the len of collabrators, if any to authors_len
                authors_len = collaborators_len
        if authors_len < 3:
            # the last attempt
            authors = get_authors_last_attempt(ref_string)
            if not authors:
                raise Undecidable("No discernible authors in '%s'"%ref_string)
            return authors
        # might have gone too far if initials are leading
        if author_pattern in [LEADING_INIT_PAT, LEADING_FULL_PAT] and authors_len >= 3:
            # if needs pruning, for example cases like
            # T.A. Heim, J. Hinze and A. R. P. Rau, J. Phys. A: Math. Theor. 42, 175203 (2009). or
            # S.K. Suslov, J. Phys. B: At. Mol. Opt. Phys. 42, 185003 (2009).
            # that captures `J. Phys` as part of author
            author_match = CROP_IF_NEEDED.search(ref_string[:authors_len])
            if author_match:
                if author_match.group(4):
                    authors_len = len(author_match.group(4))
                elif author_match.group(2):
                    authors_len = len(author_match.group(2))
                elif author_match.group(8):
                    authors_len = len(author_match.group(8))
    authors = ref_string[:authors_len].strip().strip(',')
    return authors


def get_editors(ref_string):
    """
    returns list of editors, which can appear anywhere in the reference

    :param ref_string:
    :return:
    """
    if isinstance(ref_string, str):
        ref_string = unidecode.unidecode(ref_string)

    lead_match = LEADING_INIT_AUTHORS_PAT.search(ref_string)
    trail_match = TRAILING_INIT_AUTHORS_PAT.search(ref_string)
    lead_len = trail_len = 0

    if lead_match:
        lead_len = len(lead_match.group())
    if trail_match:
        trail_len = len(trail_match.group())

    return lead_match.group().strip(',') if lead_len > trail_len else trail_match.group().strip(',') if lead_len < trail_len else None


def get_collaborators(ref_string):
    """
    collabrators are listed at the beginning of the author list,
    return the length, if there are any collaborators listed

    :param ref_string:
    :return:
    """
    match = COLLABORATION_PAT.findall(COMMA_BEFORE_AND.sub(r',\2', ref_string))
    if len(match) > 0:
        collaboration = match[-1]
        return ref_string.find(collaboration), len(collaboration)

    return 0, 0


def get_and_colleagues(ref_string):
    """

    :param ref_string:
    :return:
    """
    match = COLLEAGUES_PAT.search(ref_string)
    if match:
        andcolleagues = match.group('andcolleagues')
        return ref_string.find(andcolleagues), len(andcolleagues)

    return 0, 0


def normalize_single_author(author_string):
    """
    returns a normalized form for a single author string.

    As this is for processing author strings coming from ADS,
    we do not touch initials or similar.  This is just so ADS
    authors are at the same normalization level as what happens
    in normalize_author_list.

    :param author_string:
    :return:
    """
    return unidecode.unidecode(author_string).replace("-", " ").lower()


def normalize_author_list(author_string, initials=True):
    """
    tries to bring author_string in the form AuthorLast1; AuthorLast2

    If the function cannot make sense of author_string, it returns it unchanged.

    :param author_string:
    :param initials:
    :return:
    """
    author_string = REMOVE_AND.sub(',', author_string)
    pattern = get_author_pattern(author_string)
    if pattern:
        if initials:
            return "; ".join("%s, %s" % (match.group("last"), match.group("first")[0])
                             for match in pattern.finditer(author_string)).strip()
        else:
            return "; ".join("%s" % (match.group("last"))
                             for match in pattern.finditer(author_string)).strip()
    authors = get_authors_last_attempt(author_string)
    if authors:
        return authors
    return author_string


def get_first_author(author_string, initials=False):
    """
    returns the last name of the first author in author_string.

    If that's not possible for some reason, common.Undecidable is raised.

    :param author_string:
    :param initials:
    :return:
    """
    pattern = get_author_pattern(author_string)
    if not pattern:
        raise Undecidable('Both leading and trailing found without a majority')
    match = pattern.search(author_string)
    if initials:
        return "%s, %s" % (match.group("last"), match.group("first"))
    else:
        return match.group("last")



def get_first_author_last_name(author_string):
    """
    returns the last name of the first author of one of our normalised author strings.

    :param authors:
    :return:
    """
    if author_string:
        parts = author_string.split(';')
        if parts:
            return parts[0].split(",")[0]
    return None


def get_author_last_name_only(author_string):
    """

    :param author_string:
    :return:
    """
    try:
        return [lastname.lower().replace('-', ' ') for lastname in LAST_NAME_PAT.findall(author_string)]
    except Undecidable:
        # we are here since we have full first names, hence return every other matches
        return [single_name.lower() for name in LAST_NAME_PAT.findall(author_string) for single_name in name.split()][1::2]


def count_matching_authors(ref_authors, ads_authors, ads_first_author=None):
    """
    returns statistics on the authors matching between ref_authors
    and ads_authors.

    ads_authors is supposed to a list of ADS-normalized author strings.
    ref_authors must be a string, where we try to assume as little as
    possible about the format.  Full first names will kill this function,
    though.

    What's returned is a tuple of (missing_in_ref,
        missing_in_ads, matching_authors, first_author_missing).

    No initials verification takes place here, case is folded, everything
    is supposed to have been dumbed down to ASCII by ADS conventions.

    :param ref_authors:
    :param ads_authors:
    :param ads_first_author:
    :return:
    """
    if not ads_authors:
        raise NotImplementedError("ADS paper without authors -- what should we do?")

    matching_authors, missing_in_ref, first_author_missing = 0, 0, False

    # clean up ADS authors to only contain surnames and be lowercased
    ads_authors_lastname = [a.split(',')[0].strip().lower().replace('-', ' ')
                            for a in ads_authors]

    ref_authors_lastname = get_author_last_name_only(ref_authors)
    ref_authors = EXTRAS_PAT.sub('', ref_authors.lower().replace('.', ' '))

    if ads_first_author is None:
        ads_first_author = ads_authors_lastname[0]
    first_author_missing = ads_first_author.lower() not in ref_authors
    # compare the last name only
    if first_author_missing:
        first_author_missing = ads_first_author.split(',')[0] not in ref_authors

    different = []
    for ads_auth in ads_authors_lastname:
        if ads_auth in ref_authors or (" " in ads_auth and ads_auth.split()[-1] in ref_authors):
            matching_authors += 1
        else:
            # see if there is actually no match (check for misspelling here)
            # difference of <30% is indication of misspelling
            misspelled = False
            for ref_auth in ref_authors_lastname:
                N_max = max(len(ads_auth), len(ref_auth))
                distance = (N_max - float(editdistance.eval(ads_auth, ref_auth))) / N_max
                if distance > 0.7:
                    different.append(ref_auth)
                    misspelled = True
                    break
            if not misspelled:
                missing_in_ref += 1

    # Now try to figure out if the reference has additional authors
    # (we assume ADS author lists are complete)
    ads_authors_lastname_pattern = "|".join(ads_authors_lastname)

    if ref_authors_lastname:
        wordsNotInADS = SINGLE_WORD_EXTRACTOR.findall(re.sub(ads_authors_lastname_pattern, "", '; '.join(ref_authors_lastname)))
        # remove recognized misspelled authors
        wordsNotInADS = [word for word in wordsNotInADS if word not in different]
        missing_in_ads = len(wordsNotInADS)
    else:
        missing_in_ads = 0

    return (missing_in_ref, missing_in_ads, matching_authors, first_author_missing)


def add_author_evidence(evidences, ref_authors, ads_authors, ads_first_author, has_etal=False):
    """
    adds an evidence for ref_authors matching ads_authors.

    This is for the fielded case, where there's actually a field
    ref_authors.

    The evidence is basically the number of matching authors over
    the number of ADS authors, except when has_etal is True, in
    which case the denominator is the number of reference authors.

    :param evidences:
    :param ref_authors:
    :param ads_authors:
    :param ads_first_author:
    :param has_etal:
    :return:
    """
    ref_authors = ref_authors.replace('-', ' ')

    # note that ref_authors is a string, and we need to have at least one name to match it to
    # ads_authors with is a list, that should contain at least one name
    if len(ref_authors) == 0 or len(ads_authors) == 0:
        return
    (missing_in_ref, missing_in_ads, matching_authors, first_author_missing
     ) = count_matching_authors(ref_authors, ads_authors, ads_first_author)

    if has_etal:
        normalizer = float(matching_authors + missing_in_ads)
    else:
        normalizer = float(len(ads_authors))

    # if the first author is missing, apply the factor by which matching authors are discounted
    if first_author_missing:
        matching_authors *= current_app.config['MISSING_FIRST_AUTHOR_FACTOR']

    if normalizer != 0:
        score = round((matching_authors - missing_in_ads) / normalizer, 2)
    else:
        score = 0

    evidences.add_evidence(max(current_app.config['EVIDENCE_SCORE_RANGE'][0], min(current_app.config['EVIDENCE_SCORE_RANGE'][1], score)), "authors")

