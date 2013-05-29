import argparse
import ConfigParser
import os
import pickle

import music21
import pykov

def _txt2pickle_chain(chain_path):
    r"""
    Given a chain that is specified by a text file make a pickle of its Pykov
    representation.
    
    The result will be read much more quickly than a text file.
    """
    chain = pykov.readtrj(chain_path)
    # FIXME configure the output location!
    pickle.dump(chain, '../../../data/intermediate_results/pickle_of_' + chain_path)


def _create_chain(sig, in_folder, out_path):
    r"""
    Transform a collection of single-measure rhythms into a textual
    representation of Markov chain input.

    `sig` defines the time signature for the rhythm model to be represented.
    `in_folder` is the root folder for rhythm measures, containing subfolders
    of which at least one should be named `sig`.
    `out_path` determines the desired output location for the plain text chain.

    **Note that `out_path` is not itself the output location, because _`sig`
    will be appended to it.**

    **Also note that these chains ignore empty measures, as those do not
    represent meaningful rhythms.**
    """
    in_folder = '../../../data/intermediate_results/rhythm measures' + '/' + sig
    
    for filename in os.listdir(in_folder):
        measure = music21.converter.parse(in_folder + os.sep + filename)[1][1]
        values = [(0, None)]
        for index in range(1, len(measure)):
            print(str(measure[index]))
            if isinstance(measure[index], music21.note.Note):
                print("Found a note")
                note = measure[index]
                values.append((str(note.offset + 1),
                              str(note.quarterLength),
                              True))
            elif isinstance(measure[index], music21.note.Rest):
                print("Found a rest")
                rest = measure[index]
                values.append((str(rest.offset + 1),
                              str(rest.quarterLength),
                              False))
                              
    with open('{out_path}_{sig}'.format(**locals()), mode='w') as fh:
         for value in values:
            fh.write(str(value) + '\n')
        fh.write('END_OF_MEASURE' + '\n')


if __name__=='__main__':
    config = ConfigParser.ConfigParser()
    with open('params.ini') as param_fh:
        config.readfp(param_fh)
    chain_txt_path = config.get('Analysis', 'intermediate_rhythm_unified')
    rhythm_measures_dir = config.get('Analysis', 'intermediate_rhythm_measures_dir')

    _create_chain('common', rhythm_measures_dir, chain_txt_path)
    _txt2pickle_chain(common_chain_txt_path)
