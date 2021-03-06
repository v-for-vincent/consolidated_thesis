import collections
import ConfigParser
import functools
import logging
import logging.config
import os
import re
import shutil
import subprocess
import time
from os import kill
from signal import alarm, signal, SIGALRM, SIGKILL
from subprocess import PIPE, Popen
import sys

import music21
from music21.midi.translate import streamToMidiFile


from sight_reading.conversion.midi_handling import extract_melody_measures
from sight_reading.temperley import likelihood_melody
from sight_reading.synthesis.find_sample_difficulties import _assemble_measures, _measure_rhythms, _measure_melodies, _measure_melodies_mixed, _multi_log_likelihoods
from sight_reading.synthesis.bash import Command


MEASURES_RE = re.compile(r"""^(?P<preceding_rules>.*)(?P<measures_constraint>measures\((?P<num_measures>[0-9]+)\))(?P<following_rules>.*)$""")
GOAL_REDEF_RE = re.compile(r"""^(?P<pre_unspecified>.*(chaos\(chords,0\), ))(?P<post_unspecified>.*)$""")
MCHORD_RE = re.compile(r"""^(?P<constraint>mchord\((?P<measure_num>[0-9]+),(?P<chord>.*)\))$""")
BEAT_RE = re.compile(r"""^(?P<constraint>beat\((.+),(?P<measure_num>.+),(.+),(.+),(.+)\))$""")
ANOTE_RE = re.compile(r"""^(?P<constraint>anote\((.+),(?P<measure_num>.+),(.+),(.+),(.+)\))$""")
NOTE_RE = re.compile(r"""^(?P<constraint>note\((.+),(?P<measure_num>.+),(.+),(.+),(.+)\))$""")
OCTAVE_RE = re.compile(r"""^(?P<constraint>octave\((.+),(?P<measure_num>.+),(.+),(.+),(.+)\))$""")
NEXT_BEAT_RE = re.compile(r"""^(?P<constraint>next_beat\((.+),(?P<measure_num>.+),(.+),(.+),(.+),(.+),(.+)\))$""")
THEME_BOUNDARY_RE = re.compile(r"""^(?P<constraint>theme_boundary\((?P<theme>[a-z]),(?P<boundary>[0-9][0-9]*)\))$""")

GOAL_BACKUP_FN = 'goal_backup'
GOAL_FN = 'goal'
MIDI_FN = 'temp.midi'
RESULT_FN = 'temp.result'

_here = os.path.abspath(os.path.dirname(__file__))
runtime_path = _here.replace('src','runtime') + os.sep
logfile_path = os.path.join(runtime_path, 'logging.conf')
logging.config.fileConfig(logfile_path,
                          defaults={'this_dir': runtime_path})
LOG = logging.getLogger(__name__)


def _joinit(iterable, delimiter):
    r"""
    Auxiliary function used to intersperse an iterable with a
    delimiter.
           
    >>> list(_joinit([1, 2, 3, 4], 0))
    [1, 0, 2, 0, 3, 0, 4]
    """
    it = iter(iterable)
    yield next(it)
    for x in it:
        yield delimiter
        yield x


def _cleanup(gui_subpath, gui_path):
    r"""
    Clean up anything left over from previous runs.
    
    This involves deleting the file containing results
    from a previous run.
    
    `gui_subpath` given a filename, returns the full
    path of that file in the APOPCALEAPS GUI folder.
    The other arguments are the full paths of the goal
    file and the result file.
    """
    os.remove(gui_subpath(GOAL_FN))
    with open(gui_subpath(RESULT_FN), mode='w') as result_fh:
        pass
    for maybe_midi in os.listdir(gui_path):
        if '.midi' in maybe_midi:
            os.remove(gui_subpath(maybe_midi))


def _analyze_result_measure(gui_subpath, measure):
    r"""
    Analyze results stored about a previously generated measure.

    Specifically, this involves extracting constraints
    related to beats and notes from the results
    file and adding those which are relevant to the
    `measure`, which is the measure
    that is currently being 'fixed'.

    Chords must be extracted separately, because they
    cannot be considered in isolation.
    """
    accepted = []
    with open(gui_subpath(RESULT_FN)) as result_fh:
        for result_line in result_fh.readlines():
            if result_line.endswith(',\n'):
                result_line = result_line[0:-2]
            recent_matchers = [BEAT_RE, NEXT_BEAT_RE, ANOTE_RE, NOTE_RE, OCTAVE_RE]
            constraint_match = None
            for matcher in recent_matchers:
                constraint_match = matcher.match(result_line)
                if constraint_match:
                    break
            if constraint_match:
                found_measure = int(constraint_match.group('measure_num'))
                if found_measure == measure:
                    constraint = constraint_match.group('constraint')
                    LOG.debug("appending constraint: " + constraint)
                    accepted.append(constraint)
    return accepted


def _analyze_global_result(gui_subpath, regex):
    r"""
    Analyze information provided by a particular regular expression.

    The regular expression must describe the form of a particular
    kind of constraint exactly and must encapsulate the entire
    constraint in a regular expression group named 'constraint'.

    This information is not meant to be specific to any measure.
    """
    accepted = []
    with open(gui_subpath(RESULT_FN)) as result_fh:
        for result_line in result_fh.readlines():
            constraint_match = regex.match(result_line[0:-2])
            if constraint_match:
                constraint = constraint_match.group('constraint')
                accepted.append(constraint)
    return accepted


def _construct_goal(gui_subpath, total_measures, initial=False,
                    unspecified=None, key_mode='minor'):
    r"""
    Given a set of specifications, generate the APOPCALEAPS query
    representing the desired goal.
    """
    LOG.debug('Information about unspecified measures: {}'.format(unspecified))
    if initial:
        assert unspecified is None
        unspecified = [n for n in range(1, total_measures + 1)]
        LOG.debug('Defining every measure as unspecified')
    else:
        assert len(unspecified) >= 1
    unspecified_constraints = ['unspecified_measure({0})'.format(n)\
                               for n in unspecified]
    # filter out any doubles
    duplicate_set = set()
    unspecified_constraints = [c for c in unspecified_constraints if
                               c not in duplicate_set and not duplicate_set.add(c)]

    # get the original backup goal
    # initial goal as well as subsequent goals are extensions of original backup
    with open(gui_subpath(GOAL_BACKUP_FN+'_'+key_mode)) as backup_fh:
        for line in backup_fh.readlines():
            first_goal_match = MEASURES_RE.match(line)

    goal = first_goal_match.group('preceding_rules')
    goal += ', '.join(unspecified_constraints)
    goal += ', max_unspecified(0), '

    if initial:
        goal += 'initial, '
    else:
        specified = [n for n in range(1, total_measures + 1)\
                     if n not in unspecified] 
        chord_constraints = _analyze_global_result(gui_subpath, MCHORD_RE)
        # boundary constraints must be added starting with the highest boundary
        # otherwise, rules fire too soon
        theme_boundary_constraints = _analyze_global_result(gui_subpath,
                                                            THEME_BOUNDARY_RE)
        theme_boundary_constraints = list(reversed(theme_boundary_constraints))
        measure_constraints = [_analyze_result_measure(gui_subpath, spec)\
                               for spec in specified]
        global_constraints = chord_constraints + theme_boundary_constraints
        goal += ', '.join(global_constraints)
        goal += ', '
        for subset in measure_constraints:
            goal += ', '.join(subset)
            if len(subset) > 0:
                goal += ', '

    goal += first_goal_match.group('measures_constraint')
    return goal


def _process_goal(goal, gui_subpath, gui_path, name):
    r"""
    Transform the supplied goal into a composition.

    The composition will be output as a set of constraints,
    as a LilyPond file, a midi file. This
    function is blocking, because the goal should not change
    until these representations have been created.
    `name` determines the name of an additional midi copy that is
    output. Other files are unaffected by this and are used
    in the same way as under vanilla APOPCALEAPS. This is
    only for the sake of giving demonstrations and may be
    removed later.
    """
    while True:
        new_goal_lines = [goal]
        LOG.debug('Executing: {0}'.format(goal))
        with open(gui_subpath(GOAL_FN), 'w') as goal_fh:
            goal_fh.writelines(new_goal_lines)
        midi_path = gui_subpath(MIDI_FN)
        # create a symbolic composition
        code, stdout, stderr = run(gui_subpath('handler2'), cwd=gui_path, timeout=2)
        if not code:
            # create .ly and .midi files
            proc = subprocess.Popen(gui_subpath('handler3'), cwd=gui_path)
            proc.wait()
            shutil.copy(midi_path, gui_subpath(name + '.midi'))
            break


def run(args, cwd = None, shell = False, kill_tree = True, timeout = -1, env = None):
    '''
    Run a command with a timeout after which it will be forcibly
    killed.
    '''
    class Alarm(Exception):
        pass
    def alarm_handler(signum, frame):
        raise Alarm
    p = Popen(args, shell = shell, cwd = cwd, stdout = PIPE, stderr = PIPE, env = env)
    if timeout != -1:
        signal(SIGALRM, alarm_handler)
        alarm(timeout)
    try:
        stdout, stderr = p.communicate()
        if timeout != -1:
            alarm(0)
    except Alarm:
        pids = [p.pid]
        if kill_tree:
            pids.extend(get_process_children(p.pid))
        for pid in pids:
            # process might have died before getting to this line
            # so wrap to avoid OSError: no such process
            try: 
                kill(pid, SIGKILL)
            except OSError:
                pass
        return -9, '', ''
    return p.returncode, stdout, stderr

def get_process_children(pid):
    p = Popen('ps --no-headers -o pid --ppid %d' % pid, shell = True,
              stdout = PIPE, stderr = PIPE)
    stdout, stderr = p.communicate()
    return [int(p) for p in stdout.split()]


def _parse_themes(result_path, total_measures):
    r"""
    Scan the results of an initial run of APOPCALEAPS.
    Return a data structure indicating which themes were created and to
    which measures they apply.

    Example output: {'u' : [(1,1), (2,2), (7,7)], 'a' : [(3,4), (5,6)]}
    This would indicate that the first, second and seventh measure are not
    considered part of any motif, but that the third and fourth measure form
    an instance of motif 'a', just like the fifth and sixth measure. In other
    words, the fifth and sixth measure form a repetition or a transposition
    of the third and fourth measure.

    Also note that undefined runs are always one measure long, so the tuples
    representing undefined runs have identical first and second elements.
    """
    result_dict = collections.defaultdict(list)
    latest_entry = None # e.g. ('a', 0)
    with open(result_path) as result_fh:
        for result_line in result_fh.readlines():
            if result_line.endswith(',\n'):
                result_line = result_line[0:-2]
            boundary_match = THEME_BOUNDARY_RE.match(result_line)
            if boundary_match:
                theme = boundary_match.group('theme')
                boundary = int(boundary_match.group('boundary'))
                # previous theme instance stops right before boundary
                if latest_entry:
                    result_dict[latest_entry[0]].append((latest_entry[1], boundary))
                latest_entry = (theme, boundary + 1)
        result_dict[latest_entry[0]].append((latest_entry[1], total_measures))
    return dict(result_dict)


def _parse_key(result_path):
    r"""
    Scan the results of an initial run of APOPCALEAPS.
    Return "major" or "minor" depending on which key was used for generation.
    """
    with open(result_path) as result_fh:
        lines = result_fh.readlines()
    for result_line in lines:
        if 'key(major)' in result_line:
            return 'major'
        elif 'key(minor)' in result_line:
            return 'minor'
    raise ValueError('No key constraint is present.')


def _parse_chord(result_path, measure):
    r"""
    Scan the results of an initial run of APOPCALEAPS.
    Return the chord in the supplied measure in a notation
    that is understood by music21.
    """
    expression = r"""^mchord\({m},(?P<chord>[a-zA-Z#]+)\),$""".format(m=measure)
    LOG.debug("Regexp: {expression}".format(**locals()))
    expression = re.compile(expression)
    with open(result_path) as result_fh:
        lines = result_fh.readlines()
    for line in lines:
        match = expression.match(line.strip())
        if match:
            return music21.key.convertKeyStringToMusic21KeyString(match.group('chord'))
    raise ValueError('No chord is known for the supplied measure.')


def _generate_pre_test(gui_subpath, gui_path, result_path, total_measures, measure_num, key_mode='minor'):
    r"""
    Generate measure `measure_num`.

    This is an auxiliary function that does not check the quality of the result.
    """
    LOG.debug('Commencing generation')
    if measure_num == 1:
        goal = _construct_goal(gui_subpath, total_measures, initial=True, key_mode=key_mode)
        LOG.info("Initial goal: {goal}".format(**locals()))
    else:
        thematic_structure = _parse_themes(result_path, total_measures)
        completed_measures = _completed_measures(thematic_structure, measure_num)
        LOG.debug('Measures already completed: {}'.format(completed_measures))
        unspecified = set(range(1, total_measures + 1)) - completed_measures
        goal = _construct_goal(gui_subpath, total_measures,
                              initial=False, unspecified=unspecified, key_mode=key_mode)
        LOG.info("Goal for {measure_num}: {goal}".format(**locals()))

    _process_goal(goal, gui_subpath, gui_path,
                  'measure-{measure_num}'.format(measure_num=measure_num))


def _test_generated_measures(gui_subpath, result_path, total_measures, measure_num, rhythm_percentiles, melody_percentiles, percentile_rhythm, percentile_melody, rhythm_chain, melody_chain, mode='Relative'):
    r"""
    Check the quality of newly generated measures.

    Returns `True` if all measures meet all requirements.
    """
    LOG.debug('Commencing test')
    thematic_structure = _parse_themes(result_path, total_measures)
    measures = _assemble_measures(gui_subpath(MIDI_FN))  # 13 in all

    # these were just generated by _generate_pre_test - test these
    generated_nums = _to_be_generated(thematic_structure, measure_num)
    # these were completed before above generation
    completed_measures = _completed_measures(thematic_structure, measure_num)

    # set up stage to check last measure - rest is included in "_multi"
    recent_measure = max(generated_nums)
    existing = completed_measures | set([m for m in generated_nums if m <= recent_measure])
    LOG.debug('Existing measures, i.e. old plus new: {existing}'.format(**locals()))
    history_nums = sorted(list(existing))
    history_measures = [measures[index - 1] for index in history_nums]
    LOG.debug('As history: {history_measures}'.format(**locals()))

    tested_indices = [history_nums.index(num) for num in generated_nums]
    LOG.debug('Testing measures with indices: {tested_indices}'.format(**locals()))

    rhythm_entries_sequence = [[str(rhythm) for rhythm in _measure_rhythms(measure)] for measure in history_measures]
    rhythm_likelihoods = list(_multi_log_likelihoods(rhythm_entries_sequence, rhythm_chain))
    tested_r_likelihoods = [rhythm_likelihoods[i] for i in tested_indices]

#-----------------------------------------------------------------#
    if mode == 'Temperley':
        melody_likelihoods = []
        melody_measures = extract_melody_measures(music21.converter.parse(gui_subpath(MIDI_FN)), list(generated_nums))
        for measure in melody_measures:
            LOG.debug('Generated measure: {measure}'.format(**locals()))
            midi_f = streamToMidiFile(measure)
            midi_path = gui_subpath('temperley_measure.midi')
            # following is not a file, but a safe output location
            temp_midi_path = gui_subpath('temp_measure.midi')
            midi_f.open(midi_path, 'wb')
            midi_f.write()
            midi_f.close()
            likelihood = likelihood_melody(midi_path=gui_subpath(midi_path),
                                           temp_midi_path=temp_midi_path)
            melody_likelihoods.append(likelihood)
            LOG.debug("Adding (Temperley's) likelihood: {l}".format(l=likelihood))
        tested_m_likelihoods = melody_likelihoods  # no linear combinations...
    else:
        if mode == 'Relative':
            melody_entries_sequence = [[str(melody) for melody in _measure_melodies(measure)] for measure in history_measures]
        elif mode == 'Mixed':
            key = _parse_key(result_path)
            chords = [_parse_chord(result_path, m) for m in history_nums]
            measure_chords = zip(history_measures, chords)
            melody_entries_sequence = [[str(melody) for melody in _measure_melodies_mixed(measure, key, chord)] for (measure, chord) in measure_chords]
            LOG.debug('Melody entries sequence: {melody_entries_sequence}'.format(**locals()))
        melody_likelihoods = list(_multi_log_likelihoods(melody_entries_sequence, melody_chain))
        tested_m_likelihoods = [melody_likelihoods[i] for i in tested_indices]
#-----------------------------------------------------------------#

    LOG.debug('Likelihoods for tested rhythms: {tested_r_likelihoods}'.format(**locals()))
    LOG.debug('Likelihoods for tested melodies: {tested_m_likelihoods}'.format(**locals()))

    generated_rhythm_percentiles = [belongs_to_percentile(likelihood, rhythm_percentiles) for likelihood in tested_r_likelihoods]
    generated_melody_percentiles = [belongs_to_percentile(likelihood, melody_percentiles) for likelihood in tested_m_likelihoods]

    LOG.debug('Percentile sections for rhythms: {generated_rhythm_percentiles}'.format(**locals()))
    LOG.debug('Percentile sections for melodies: {generated_melody_percentiles}'.format(**locals()))

    right_rhythm = True#all((percentile == percentile_rhythm for percentile in generated_rhythm_percentiles))
    right_melody = True#all((percentile == percentile_melody for percentile in generated_melody_percentiles))

    return (right_rhythm and right_melody)


def _generate_with_test(gui_subpath, gui_path, result_path, total_measures, measure_num, rhythm_percentiles, melody_percentiles, percentile_rhythm, percentile_melody, rhythm_chain, melody_chain, test=True, mode='Relative', key_mode='minor'):
    r"""
    Generate a piece that satisfies supplied constraints.

    Specifically, recycle information from measures < `measure_num`.
    Also recycle information from duplicated measures >= `measure_num`
    if the last measure of the first repetition of the theme under
    consideration < `measure_num`.

    If a generated piece does not meet the supplied constraints,
    this function will try to generate the new measure again.
    Therefore, it will eventually return as long as the supplied constraints
    can be met.
    """
    LOG.debug('Generating with test: {num}'.format(num=measure_num))
    attempt = 1
    while True:
        _generate_pre_test(gui_subpath, gui_path, result_path, total_measures, measure_num, key_mode)
        if test and measure_num != 13:
            if _test_generated_measures(gui_subpath, result_path, total_measures, measure_num, rhythm_percentiles, melody_percentiles, percentile_rhythm, percentile_melody, rhythm_chain, melody_chain, mode):
                return attempt
        else:
            # don't check (always easy) closing measure
            return attempt
        attempt += 1


def _completed_measures(thematic_structure, measure_num):
    r"""
    Given the thematic structure of a piece and the next measure to be
    generated, determine which measures can be safely copied from a
    previous iteration.

    Measures can be copied for two reasons: either because we have already
    iterated past them, or because they are transpositions of previously
    completed themes.

    >>> _completed_measures({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 4)
    set([1, 2, 3, 5, 6])
    >>> _completed_measures({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 5)
    set([1, 2, 3, 4, 5, 6])
    >>> _completed_measures({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 1)
    set([])
    >>> _completed_measures({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 2)
    set([1, 5])
    """
    through_themes = set()
    for theme in (theme for theme in thematic_structure if theme != 'u'):
        for instantiation in thematic_structure[theme]:
            if instantiation[1] < measure_num:
                for inst in thematic_structure[theme]:
                    through_themes = through_themes | set(range(inst[0], inst[1] + 1))
            elif instantiation[0] < measure_num:
                diff = measure_num - instantiation[0]
                for inst in thematic_structure[theme]:
                    through_themes = through_themes | set(range(inst[0], inst[0] + diff))

    return set(range(1, measure_num)) | set(through_themes)
        

def _to_be_generated(thematic_structure, measure_num):
    r"""
    >>> _to_be_generated({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 1)
    set([1, 5])
    >>> _to_be_generated({'a': [(1, 2), (5, 6)], 'b': [(3, 4)]}, 5)
    set([])
    """
    return _completed_measures(thematic_structure, measure_num + 1) - _completed_measures(thematic_structure, measure_num)


def belongs_to_percentile(log_likelihood, percentiles):
    r"""
    Given a list of percentile boundaries that go from most likely to least
    likely (i.e. from near-zero log likelihood to more negative)
    determine to which category the supplied log_likelihood belongs.

    If the supplied likelihood is more negative than the last boundary, an
    imaginary -Inf boundary is used for the n+1st percentile.
    >>> belongs_to_percentile(0.0, [-2.0, -4.0])
    0
    >>> belongs_to_percentile(-10.0, [-2.0, -4.0])
    2
    """
    for boundary_index in range(0, len(percentiles)):
        if log_likelihood > percentiles[boundary_index]:
            return boundary_index
    return len(percentiles)


def compose(music_path, rhythm_chain, rhythm_percentiles, percentile_r,
            melodic_chain, melody_percentiles, percentile_m, mode, key_mode
            ):
    r"""
    Compose a new piece in an iterative fashion.

    Several pieces of information are required:

       #. `music_path`: the path to the APOPCALEAPS folder with music.chrism
       #. `rhythm_chain`: a Pykov chain to assess probability of rhythm
       #. `rhythm_percentiles`: a sequence of *log* rhythm probabilities, e.g.
          [-2.30, -4.60] if easier half has log probability greater than -2.30
       #. `percentile_r`: the index of the desired percentile, e.g. 1 if the
          0 for the easier half and 1 for the more difficult half.
          Also note that it is permitted to supply the value 2 to attempt to
          obtain a piece whose rhythms are more difficult than any seen in the
          corpus on which percentiles are based.
       #. `melodic_chain`: a Pykov chain to assess probability of melody
       #. `melody_percentiles`: analogous with `rhythm_percentiles`
       #. `percentile_m`: analogous with `percentile_r`
       #. `mode`: indicates how to evaluate melodic difficulty
       #. `key_mode`: whether to compose a minor or major piece
    """
    gui_path = os.path.join(music_path, 'gui')
    gui_subpath = functools.partial(os.path.join, gui_path)
    result_path = gui_subpath(RESULT_FN)
    total_measures = 13
    _cleanup(gui_subpath, gui_path)

    # don't loop from 1 because we can save work when using themes
    attempts_per_measure = []
    attempts = _generate_with_test(gui_subpath, gui_path, result_path, total_measures, 1, rhythm_percentiles, melody_percentiles, percentile_r, percentile_m, rhythm_chain, melodic_chain, True, mode, key_mode)
    attempts_per_measure.append(attempts)
    thematic_structure = _parse_themes(result_path, total_measures)

    for measure_num in range(2, total_measures + 1):
        if measure_num not in _completed_measures(thematic_structure, measure_num):
            attempts = _generate_with_test(gui_subpath, gui_path, result_path, total_measures, measure_num, rhythm_percentiles, melody_percentiles, percentile_r, percentile_m, rhythm_chain, melodic_chain, test=True, mode=mode, key_mode=key_mode)
        attempts_per_measure.append(attempts)

    filename = '{percentile_r}_{percentile_m}_{key_mode}.ly'
    timestamp = time.time()
    attempts_filename = '{timestamp}_attempts.txt'.format(timestamp=timestamp)
    with open(attempts_filename, mode='w') as fh:
        fh.write(str(attempts_per_measure))
    shutil.copy(gui_subpath('temp.ly'), filename.format(**locals()))


def compose_samples(music_path, number_samples):
    r"""
    Compose a number of samples of APOPCALEAPS output.

    This function does not work iteratively.
    It performs one run for each sample.
    """
    gui_path = os.path.join(music_path, 'gui')
    gui_subpath = functools.partial(os.path.join, gui_path)
    result_path = gui_subpath(RESULT_FN)
    total_measures = 13
    _cleanup(gui_subpath, gui_path)

    for i in range(1, number_samples + 1):
        _generate_with_test(gui_subpath, gui_path, result_path, total_measures, 1, None, None, None, None, None, None, test=False, key_mode='minor') 
        shutil.copy(gui_subpath('measure-1.midi'), gui_subpath('sample-' + str(i) + '.midi'))
     

if __name__ == '__main__':
    import doctest
    doctest.testmod()
