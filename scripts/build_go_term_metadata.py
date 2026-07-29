#! /usr/bin/env python
"""Build the GO term metadata for the GO term prediction model from go-basic.obo file
and for outputs/label_matrix_top500/term_names.npy GO term names.
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

def parse_obo(go_basic_obo_file: Path) -> list[dict]:
    """Parse a go-basic.obo file into a list of term dicts."""

    terms = []
    current = None
    in_term = False

    with open(go_basic_obo_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            stripped = line.strip()

            if stripped.startswith('['):
                if current is not None and 'id' in current:
                    terms.append(current)
                if stripped == '[Term]':
                    current = {}
                    in_term = True
                else:
                    current = None
                    in_term = False
                continue

            if not in_term or stripped == '':
                continue

            if ':' not in line:
                continue

            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()

            if key == 'id':
                current['id'] = value
            elif key == 'name':
                current['name'] = value
            elif key == 'namespace':
                current['namespace'] = value
            elif key == 'def':
                if value.startswith('"'):
                    end_quote = value.find('"', 1)
                    current['def'] = value[1:end_quote]
                else:
                    current['def'] = value

        if current is not None and 'id' in current:
            terms.append(current)

    return terms


def build_dataframe(obo_path, npy_path):
    """Load the array of GO term names we want to filter on."""
    term_names = np.load(npy_path, allow_pickle=True)
    term_names_set = set(term_names)

    all_terms = parse_obo(obo_path)

    rows = []
    for t in all_terms:
        go_id = t.get('id')
        if go_id is not None and go_id in term_names_set:
            rows.append({
                'GO_term': go_id,
                'name': t.get('name'),
                'namespace': t.get('namespace'),
                'def': t.get('def'),
            })

    df = pd.DataFrame(rows, columns=['GO_term', 'name', 'namespace', 'def'])
    return df


def make_dir_if_not_exists(path: Path, exist_ok: bool = True) -> None:
    """Make a directory if it doesn't exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=exist_ok)


def main():
    parser = argparse.ArgumentParser(description="Build the GO term metadata for the GO term prediction model from go-basic.obo file and for outputs/label_matrix_top500/term_names.npy GO term names.")
    parser.add_argument(
        "--go-basic-obo-file", 
        type=str, 
        default="data/cafa-6-protein-function-prediction/Train/go-basic.obo", 
        help="Path to the go-basic.obo file")
    parser.add_argument(
        "--term-names-file", 
        type=str, 
        default="outputs/label_matrix_top500/term_names.npy", 
        help="Path to the term_names.npy file")
    parser.add_argument(
        "--output-file", 
        type=str, 
        default="services/streamlit-ui/metadata/go_term_metadata.csv", 
        help="Path to the output file")
    args = parser.parse_args()
    
    go_basic_obo_file = Path(args.go_basic_obo_file)
    term_names_file = Path(args.term_names_file)

    make_dir_if_not_exists(Path(args.output_file).parent)

    df = build_dataframe(go_basic_obo_file, term_names_file)
    df.to_csv(args.output_file, index=False)

    print(f"GO term metadata saved to {args.output_file}")

if __name__ == "__main__":
    main()