"""Counterexamples for frozen v2 matching; not a replacement evaluation metric."""

from scripts.evidence.metrics_v2 import boundary_equivalent, one_to_one_match


def demonstrate():
    text = 'soil organic carbon stocks concentration'
    support = {'source': 'abstract', 'evidence_text': text, 'start': 0, 'end': len(text)}
    gold = [{'value': value, 'support': support} for value in ('soil organic carbon', 'carbon stocks')]
    predicted = [{'value': value, 'support': support} for value in
                 ('soil organic carbon stocks', 'soil organic carbon concentration')]
    forward = len(one_to_one_match(gold, predicted, text)[0])
    reverse = len(one_to_one_match(gold, list(reversed(predicted)), text)[0])
    sentence = 'not increase rice yield'
    negation_support = {'source': 'abstract', 'evidence_text': sentence, 'start': 0, 'end': len(sentence)}
    negation = boundary_equivalent({'value': 'increase rice yield', 'support': negation_support},
                                   {'value': sentence, 'support': negation_support}, sentence)
    return {'description': 'Synthetic diagnostic only; does not re-score historical benchmarks.',
            'forward_matches': forward, 'reversed_matches': reverse,
            'negation_pair_matched': negation}


if __name__ == '__main__':
    import json
    print(json.dumps(demonstrate(), indent=2))
