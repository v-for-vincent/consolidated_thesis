%% -*- Prolog -*-

/*
========================================================================
  
This module provides a simple interface to the B-Prolog compiler.
In the following description, <Prog> denotes a program represented in
the B-Prolog internal form (i.e. a list of pred/6).

$pp_bpif_read_program(-Prog,+File) :-
    Loads <Prog> from <File>.

$pp_bpif_compile_program(+Prog,+File) :-
    Compiles <Prog> and saves the resultant byte-code into <File>.

========================================================================
*/

%%--------------------------------
%%  Entry Point

$pp_bpif_read_program(Prog,File) :-
    getclauses1(File,Prog,0).

$pp_bpif_compile_program(Prog0,File) :-
    $pp_preproc_program(Prog0,Prog1),
    phase_1_process(Prog1,Prog2),
    compileProgToFile(_,File,Prog2).


%%--------------------------------
%%  Preprocessing

$pp_preproc_program(Prog0,Prog1) :-
    new_hashtable(AuxTable),
    $pp_preproc_program(Prog0,Prog1,AuxTable,0).

$pp_preproc_program(Prog0,Prog1,AuxTable,K),
      Prog0 = [pred(F,N,M,D,T,Cls0)|Prog0R] =>
    Prog1 = [pred(F,N,M,D,T,Cls1)|Prog1R],
    $pp_preproc_clauses(Cls0,Cls1,AuxTable,K,NewK),
    $pp_preproc_program(Prog0R,Prog1R,AuxTable,NewK).
$pp_preproc_program(Prog0,Prog1,AuxTable,_),
      Prog0 = [] =>
    hashtable_values_to_list(AuxTable,Prog1).

$pp_preproc_clauses(Cls0,Cls1,AuxTable,K,NewK), Cls0 = [Cl0|Cls0R] =>
    Cls1 = [Cl1|Cls1R],
    preprocess_cl(Cl0,Cl1,AuxTable,K,TmpK,1),
    $pp_preproc_clauses(Cls0R,Cls1R,AuxTable,TmpK,NewK).
$pp_preproc_clauses(Cls0,Cls1,_,K,NewK), Cls0 = [] =>
    Cls1 = [],
    K = NewK.


%%--------------------------------
%%  Loading programs at runtime

% erase all solutions in the current table unconditionally
$pp_consult_preds(Preds,Prog) :-
    $pp_consult_preds_aux(Preds,Prog),
    c_INITIALIZE_TABLE,!.

% erase solutions depending on the clean_table flag
$pp_consult_preds_cond(Preds,Prog) :-
    $pp_consult_preds_aux(Preds,Prog),
    ( get_prism_flag(clean_table,on) -> c_INITIALIZE_TABLE
    ; true
    ),!.

$pp_consult_preds_aux([],Prog) :- var(Prog),!.
$pp_consult_preds_aux([],Prog) :-
    closetail(Prog),
    phase_1_process(Prog,Prog2),
    $pd_tmp_out(TmpOutBase,TmpOutFull),
    $output_file(TmpOutBase,OutFile),
    compileProgToFile(tmp,OutFile,Prog2),
    b_LOAD_cfc(OutFile,_,0),
    ( file_exists(TmpOutFull) -> delete_file(TmpOutFull)
    ; true
    ),!.
$pp_consult_preds_aux([Pred|Preds],CompiledProg):-
    consult_pred(Pred,CompiledProg),!,
    $pp_consult_preds_aux(Preds,CompiledProg).
