/*  $Id: chr_swi.pl,v 1.26 2006/04/19 13:45:23 jan Exp $

    Part of CHR (Constraint Handling Rules)

    Author:        Tom Schrijvers and Jan Wielemaker
    E-mail:        Tom.Schrijvers@cs.kuleuven.be
    WWW:           http://www.swi-prolog.org
    Copyright (C): 2003-2004, K.U. Leuven

    This program is free software; you can redistribute it and/or
    modify it under the terms of the GNU General Public License
    as published by the Free Software Foundation; either version 2
    of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU Lesser General Public
    License along with this library; if not, write to the Free Software
    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

    As a special exception, if you link this library with other files,
    compiled with a Free Software compiler, to produce an executable, this
    library does not by itself cause the resulting executable to be covered
    by the GNU General Public License. This exception does not however
    invalidate any other reasons why the executable file might be covered by
    the GNU General Public License.
*/

%% SWI begin
:- module(chr,
	  [ op(1180, xfx, ==>),
	    op(1180, xfx, <=>),
	    op(1150, fx, constraints),
	    op(1150, fx, chr_constraint),
	    op(1150, fx, chr_constraint),
	    op(1150, fx, handler),
	    op(1150, fx, rules),
	    op(1100, xfx, \),
	    op(1200, xfx, @),
	    op(1190, xfx, pragma),
	    op( 500, yfx, #),
	    op(1150, fx, chr_type),
	    op(1130, xfx, --->),
	    op(1150, fx, (?)),
	    chr_show_store/1,		% +Module
	    find_chr_constraint/1,	% +Pattern
	    chr_trace/0,
	    chr_notrace/0,
	    chr_leash/1			% +Ports
	  ]).

% --NF :- set_prolog_flag(generate_debug_info, false).


:- multifile user:file_search_path/2.
:- dynamic   user:file_search_path/2.
:- dynamic   chr_translated_program/1.

user:file_search_path(chr, library(chr)).

/*
:- load_files([ library(gensym),
		chr(chr_translate),
		chr(chr_runtime),
		chr(chr_messages),
		chr(chr_hashtable_store),
		chr(chr_compiler_errors)
	      ],
	      [ if(not_loaded),
		silent(true)
	      ]).
*/

:- use_module(library(lists),[member/2]).
%% SWI end

%% SICStus begin
%% :- module(chr,[]).
%% 
%% :- op(1180, xfx, ==>),
%% 	op(1180, xfx, <=>),
%% 	op(1150, fx, constraints),
%% 	op(1150, fx, handler),
%% 	op(1150, fx, rules),
%% 	op(1100, xfx, \),
%% 	op(1200, xfx, @),
%% 	op(1190, xfx, pragma),
%% 	op( 500, yfx, #),
%% 	op(1150, fx, chr_type),
%% 	op(1130, xfx, --->),
%% 	op(1150, fx, (?)).
%% 
%% :- multifile user:file_search_path/2.
%% :- dynamic   user:file_search_path/2.
%% :- dynamic   chr_translated_program/1.
%% 
%% user:file_search_path(chr, library(chr)).
%% 
%% 
%% :- use_module('chr/chr_translate').
%% :- use_module('chr/chr_runtime').
%% :- use_module('chr/chr_messages').
%% :- use_module('chr/chr_hashtable_store').
%% :- use_module('chr/hprolog').
%% :- use_module(library(lists),[member/2, memberchk/2]).
%% SICStus end


:- dynamic
	chr_term/2.			% File, Term

%	chr_expandable(+Term)
%	
%	Succeeds if Term is a  rule  that   must  be  handled by the CHR
%	compiler. Ideally CHR definitions should be between
%
%		:- constraints ...
%		...
%		:- end_constraints.
%
%	As they are not we have to   use  some heuristics. We assume any
%	file is a CHR after we've seen :- constraints ... 

chr_expandable((:- constraints _)).
chr_expandable((constraints _)).
chr_expandable((:- chr_constraint _)).
chr_expandable((:- chr_type _)).
chr_expandable((chr_type _)).
chr_expandable(option(_, _)).
chr_expandable((:- chr_option(_, _))).
chr_expandable((handler _)).
chr_expandable((rules _)).
chr_expandable((_ <=> _)).
chr_expandable((_ @ _)).
chr_expandable((_ ==> _)).
chr_expandable((_ pragma _)).

%	chr_expand(+Term, -Expansion)
%	
%	Extract CHR declarations and rules from the file and run the
%	CHR compiler when reaching end-of-file.

%% SWI begin
extra_declarations([(:- use_module(chr(chr_runtime))),
		    (:- style_check(-discontiguous)), % no need to restore; file ends
		    (:- set_prolog_flag(generate_debug_info, false))
		   | Tail], Tail).
%% SWI end

%% SICStus begin
%% extra_declarations([(:-use_module(chr(chr_runtime)))
%% 		     , (:- use_module(library(terms),[term_variables/2]))
%% 		     , (:-use_module(chr(hpattvars)))
%% 		     | Tail], Tail).		   
%% SICStus end

chr_expand(Term, []) :-
	chr_expandable(Term), !,
	prolog_load_context(file,File),
	assert(chr_term(File, Term)).
chr_expand(end_of_file, FinalProgram) :-
	extra_declarations(FinalProgram,Program),
	prolog_load_context(file,File),
	findall(T, retract(chr_term(File, T)), CHR0),
	CHR0 \== [],
	prolog_load_context(module, Module),
	add_debug_decl(CHR0, CHR1),
	add_optimise_decl(CHR1, CHR),
	catch(call_chr_translate(File,
			   [ (:- module(Module, []))
			   | CHR
			   ],
			   Program0),
		chr_error(Error),
		(	chr_compiler_errors:print_chr_error(Error),
			fail
		)
	),
	delete_header(Program0, Program).


delete_header([(:- module(_,_))|T0], T) :- !,
	delete_header(T0, T).
delete_header(L, L).

add_debug_decl(CHR, CHR) :-
	member(option(Name, _), CHR), Name == debug, !.
add_debug_decl(CHR, CHR) :-
	member((:- chr_option(Name, _)), CHR), Name == debug, !.
add_debug_decl(CHR, [(:- chr_option(debug, Debug))|CHR]) :-
	(   chr_current_prolog_flag(generate_debug_info, true)
	->  Debug = on
	;   Debug = off
	).

%% SWI begin
chr_current_prolog_flag(Flag,Val) :- current_prolog_flag(Flag,Val).
%% SWI end

%% SICStus begin
%% chr_current_prolog_flag(generate_debug_info, _) :- fail.
%% chr_current_prolog_flag(optimize,full).
%% chr_current_prolog_flag(chr_toplevel_show_store,true).
%% SICStus end

%% B-Prolog begin
chr_current_prolog_flag(generate_debug_info, _) :- fail.
chr_current_prolog_flag(chr_toplevel_show_store,true).
%% B-Prolog end



add_optimise_decl(CHR, CHR) :-
	\+(\+(memberchk((:- chr_option(optimize, _)), CHR))), !.
add_optimise_decl(CHR, [(:- chr_option(optimize, full))|CHR]) :-
	chr_current_prolog_flag(optimize, full), !.
add_optimise_decl(CHR, CHR).


%	call_chr_translate(+File, +In, -Out)
%	
%	The entire chr_translate/2 translation may fail, in which case we'd
%	better issue a warning  rather  than   simply  ignoring  the CHR
%	declarations.

call_chr_translate(_, In, _Out) :-
	( chr_translate(In, Out0) ->
	    nb_setval(chr_translated_program,Out0),
	    fail
	).
call_chr_translate(_, _In, Out) :-
	nb_current(chr_translated_program,Out), !,
	nb_delete(chr_translated_program).

call_chr_translate(File, _, []) :-
	print_message(error, chr(compilation_failed(File))).


		 /*******************************
		 *      SYNCHRONISE TRACER	*
		 *******************************/

:- multifile
	user:message_hook/3,
	chr:debug_event/2,
	chr:debug_interact/3.
:- dynamic
	user:message_hook/3.

user:message_hook(trace_mode(OnOff), _, _) :-
	(   OnOff == on
	->  chr_trace
	;   chr_notrace
	),
	fail.				% backtrack to other handlers

%	chr:debug_event(+State, +Event)
%	
%	Hook into the CHR debugger.  At this moment we will discard CHR
%	events if we are in a Prolog `skip' and we ignore the 

chr:debug_event(_State, _Event) :-
	tracing,			% are we tracing?
	prolog_skip_level(Skip, Skip),
	Skip \== very_deep,
	prolog_current_frame(Me),
	prolog_frame_attribute(Me, level, Level),
	Level > Skip, !.

%	chr:debug_interact(+Event, +Depth, -Command)
%	
%	Hook into the CHR debugger to display Event and ask for the next
%	command to execute. This  definition   causes  the normal Prolog
%	debugger to be used for the standard ports.

chr:debug_interact(Event, _Depth, creep) :-
	prolog_event(Event),
	tracing, !.

prolog_event(call(_)).
prolog_event(exit(_)).
prolog_event(fail(_)).




		 /*******************************
		 *	      MESSAGES		*
		 *******************************/

:- multifile
	prolog:message/3.

prolog:message(chr(CHR)) -->
	chr_message(CHR).

		 /*******************************
		 *	 TOPLEVEL PRINTING	*	
		 *******************************/

%% SWI begin
%:- set_prolog_flag(chr_toplevel_show_store,true).
%% SWI end

:- multifile chr:'$chr_module'/1.

prolog:message(query(YesNo)) --> !,
	['~@'-[once(chr:print_all_stores)]],
        '$messages':prolog_message(query(YesNo)).

prolog:message(query(YesNo,Bindings)) --> !,
	['~@'-[once(chr:print_all_stores)]],
        '$messages':prolog_message(query(YesNo,Bindings)).

print_all_stores :-
	( chr_current_prolog_flag(chr_toplevel_show_store,true),
	  catch(nb_getval(chr_global, _), _, fail),
	  chr:'$chr_module'(Mod),
	  chr_show_store(Mod),
	  fail
	;
	  true
	).

		 /*******************************
		 *	   MUST BE LAST!	*
		 *******************************/

:- multifile user:term_expansion/2.
:- dynamic   user:term_expansion/2.

user:term_expansion(In, Out) :-
	chr_expand(In, Out).
	

