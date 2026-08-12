#!/usr/bin/env python3
"""
Unit tests for run_summary data structures.

Tests the RunSummary and PhaseResult classes for proper serialization,
deserialization, and data management functionality.
"""

import pytest
import json
import tempfile
from datetime import datetime
from pathlib import Path

from solver_support.run_summary import (
    RunSummary, PhaseResult, create_run_summary, create_phase_result
)


class TestPhaseResult:
    """Test the PhaseResult dataclass."""
    
    def test_phase_result_creation(self):
        """Test basic PhaseResult creation."""
        phase = PhaseResult(
            name="solving",
            command=["savilerow", "model.eprime", "problem.param"],
            duration=12.5,
            success=True
        )
        
        assert phase.name == "solving"
        assert phase.command == ["savilerow", "model.eprime", "problem.param"]
        assert phase.duration == 12.5
        assert phase.success is True
        assert phase.output_files == []
        assert phase.error_message == ""
        assert phase.peak_memory_kb is None
        assert phase.solver_stats == {}
    
    def test_phase_result_defaults(self):
        """Test PhaseResult with default values."""
        phase = PhaseResult(name="translation")
        
        assert phase.name == "translation"
        assert phase.command == []
        assert phase.duration == 0.0
        assert phase.success is False
        assert phase.output_files == []
        assert phase.error_message == ""
        assert phase.peak_memory_kb is None
        assert phase.solver_stats == {}
    
    def test_phase_result_with_stats(self):
        """Test PhaseResult with solver statistics."""
        phase = PhaseResult(
            name="solving",
            command=["fd", "domain.pddl", "problem.pddl"],
            duration=5.2,
            success=True,
            solver_stats={
                'expanded_states': 713,
                'plan_length': 4,
                'solution_found': True
            }
        )
        
        assert phase.solver_stats['expanded_states'] == 713
        assert phase.solver_stats['plan_length'] == 4
        assert phase.solver_stats['solution_found'] is True
    
    def test_phase_result_to_dict(self):
        """Test PhaseResult serialization to dictionary."""
        phase = PhaseResult(
            name="solving",
            command=["savilerow", "model.eprime"],
            duration=10.0,
            success=True,
            output_files=["solution.txt"],
            error_message="",
            peak_memory_kb=1024,
            solver_stats={'horizon_number': 1}
        )
        
        result_dict = phase.to_dict()
        
        assert result_dict['name'] == "solving"
        assert result_dict['command'] == ["savilerow", "model.eprime"]
        assert result_dict['duration'] == 10.0
        assert result_dict['success'] is True
        assert result_dict['output_files'] == ["solution.txt"]
        assert result_dict['error_message'] == ""
        assert result_dict['peak_memory_kb'] == 1024
        assert result_dict['solver_stats'] == {'horizon_number': 1}

    def test_phase_result_outcome_fields_default_to_unrecorded(self):
        """A hand-built phase says nothing about how a process ended."""
        phase = PhaseResult(name="solving")

        assert phase.returncode is None
        assert phase.timed_out is False
        assert phase.launch_failed is False

    def test_phase_result_outcome_fields_round_trip(self):
        """The outcome survives a save/load, so a reader of the JSON can tell a
        timeout from a solver that ran and failed."""
        phase = PhaseResult(
            name="solving",
            command=["savilerow", "model.eprime"],
            success=False,
            returncode=-1,
            timed_out=True,
        )

        restored = PhaseResult(**phase.to_dict())

        assert restored.returncode == -1
        assert restored.timed_out is True
        assert restored.launch_failed is False

    def test_phase_result_loads_a_summary_written_before_these_fields(self):
        """Older summaries have no outcome keys at all; they must still load."""
        legacy = {
            'name': 'solving',
            'command': ['savilerow', 'model.eprime'],
            'duration': 10.0,
            'success': True,
            'output_files': [],
            'error_message': '',
            'peak_memory_kb': None,
            'solver_stats': {},
            'warnings': [],
        }

        phase = PhaseResult(**legacy)

        assert phase.success is True
        assert phase.returncode is None
        assert phase.timed_out is False


class TestRunSummary:
    """Test the RunSummary dataclass."""
    
    def test_run_summary_creation(self):
        """Test basic RunSummary creation."""
        summary = RunSummary(
            instance="sample-instance",
            model="demo",
            solver="savilerow-native",
            timestamp="202506181200.12345"
        )
        
        assert summary.instance == "sample-instance"
        assert summary.model == "demo"
        assert summary.solver == "savilerow-native"
        assert summary.timestamp == "202506181200.12345"
        assert summary.start_time is None
        assert summary.end_time is None
        assert summary.total_duration == 0.0
        assert summary.success is False
        assert summary.solution_found is False
        assert summary.final_status == ""
        assert summary.phases == []
    
    def test_run_summary_defaults(self):
        """Test RunSummary with default values."""
        summary = RunSummary()
        
        assert summary.instance == ""
        assert summary.model == ""
        assert summary.solver == ""
        assert summary.timestamp == ""
        assert summary.phases == []
    
    def test_add_phase(self):
        """Test adding phases to RunSummary."""
        summary = RunSummary()
        
        phase1 = PhaseResult(name="translation", duration=0.5, success=True)
        phase2 = PhaseResult(
            name="solving",
            duration=12.0,
            success=True,
            solver_stats={'solution_found': True}
        )
        
        summary.add_phase(phase1)
        summary.add_phase(phase2)
        
        assert len(summary.phases) == 2
        assert summary.total_duration == 12.5
        assert summary.solution_found is True

    def test_add_phase_ignores_solution_found_from_translation(self):
        """A non-solving phase must not set solution_found.

        Regression: the translation phase runs a consumer's converter, whose output
        (a letting file with English comments) can make the generic stats
        extractor substring-match a word like "optimal" and report
        solution_found=True. Only solving phases may set the run-level flag.
        """
        summary = RunSummary()

        # Translation phase carrying a spurious solution_found (as the generic
        # extractor would produce from a comment containing "optimal").
        summary.add_phase(PhaseResult(
            name="translation",
            duration=0.5,
            success=True,
            solver_stats={'solution_found': True},
        ))
        assert summary.solution_found is False  # translation must not latch it

        # A genuine solving phase still sets it.
        summary.add_phase(PhaseResult(
            name="solving-horizon-7",
            duration=2.0,
            success=True,
            solver_stats={'solution_found': True},
        ))
        assert summary.solution_found is True

    def test_finalize(self):
        """Test finalizing a RunSummary."""
        summary = RunSummary()
        summary.start_time = datetime(2025, 6, 18, 12, 0, 0)
        
        phase1 = PhaseResult(name="translation", success=True)
        phase2 = PhaseResult(name="solving", success=True)
        summary.add_phase(phase1)
        summary.add_phase(phase2)
        
        summary.finalize("completed")
        
        assert summary.final_status == "completed"
        assert summary.success is True  # All phases succeeded
        assert summary.end_time is not None
    
    def test_finalize_with_failure(self):
        """Test finalizing a RunSummary with phase failures."""
        summary = RunSummary()
        
        phase1 = PhaseResult(name="translation", success=True)
        phase2 = PhaseResult(name="solving", success=False)
        summary.add_phase(phase1)
        summary.add_phase(phase2)
        
        summary.finalize("failed")

        assert summary.final_status == "failed"
        assert summary.success is False  # One phase failed

    def test_finalize_hlim_forces_failure(self):
        """'hlim' (horizon scan exhausted) is a no-solution outcome: not a success.

        Even if every subprocess phase exited cleanly, reaching the horizon cap
        without a solution must report success False, while keeping the distinct
        'hlim' status so it is not confused with a proven-unsat 'failed'.
        """
        summary = RunSummary()

        phase1 = PhaseResult(name="translation", success=True)
        phase2 = PhaseResult(name="solving", success=True)
        summary.add_phase(phase1)
        summary.add_phase(phase2)

        summary.finalize("hlim")

        assert summary.final_status == "hlim"
        assert summary.success is False
    
    def test_get_phase(self):
        """Test getting phases by name."""
        summary = RunSummary()
        
        translation = PhaseResult(name="translation", duration=0.5)
        solving = PhaseResult(name="solving", duration=12.0)
        summary.add_phase(translation)
        summary.add_phase(solving)
        
        assert summary.get_phase("translation") == translation
        assert summary.get_phase("solving") == solving
        assert summary.get_phase("visualisation") is None
    
    def test_get_solving_time(self):
        """Test getting solving phase duration."""
        summary = RunSummary()
        
        summary.add_phase(PhaseResult(name="translation", duration=0.5))
        summary.add_phase(PhaseResult(name="solving", duration=12.0))
        
        assert summary.get_solving_time() == 12.0
    
    def test_get_translation_time(self):
        """Test getting translation phase duration."""
        summary = RunSummary()
        
        summary.add_phase(PhaseResult(name="translation", duration=0.5))
        summary.add_phase(PhaseResult(name="solving", duration=12.0))
        
        assert summary.get_translation_time() == 0.5
    
    def test_get_time_no_phase(self):
        """Test getting time for non-existent phases."""
        summary = RunSummary()
        
        assert summary.get_solving_time() == 0.0
        assert summary.get_translation_time() == 0.0


class TestRunSummarySerialization:
    """Test RunSummary JSON serialization and deserialization."""
    
    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        summary = RunSummary(
            instance="test",
            model="compact",
            total_duration=15.5
        )
        
        result = summary.to_dict()
        
        assert result['instance'] == "test"
        assert result['model'] == "compact"
        assert result['total_duration'] == 15.5
        assert result['start_time'] is None
        assert result['end_time'] is None
    
    def test_to_dict_with_datetime(self):
        """Test to_dict with datetime objects."""
        start_time = datetime(2025, 6, 18, 12, 0, 0, 123456)
        end_time = datetime(2025, 6, 18, 12, 2, 15, 654321)
        
        summary = RunSummary(
            instance="test",
            start_time=start_time,
            end_time=end_time
        )
        
        result = summary.to_dict()
        
        assert result['start_time'] == start_time.isoformat()
        assert result['end_time'] == end_time.isoformat()
    
    def test_to_json(self):
        """Test JSON serialization."""
        summary = RunSummary(
            instance="test",
            model="compact",
            total_duration=15.5
        )
        
        json_str = summary.to_json()
        parsed = json.loads(json_str)
        
        assert parsed['instance'] == "test"
        assert parsed['model'] == "compact"
        assert parsed['total_duration'] == 15.5
    
    def test_from_dict_basic(self):
        """Test basic from_dict conversion."""
        data = {
            'instance': 'test',
            'model': 'compact',
            'total_duration': 15.5,
            'start_time': None,
            'end_time': None,
            'phases': []
        }
        
        summary = RunSummary.from_dict(data)
        
        assert summary.instance == "test"
        assert summary.model == "compact"
        assert summary.total_duration == 15.5
        assert summary.start_time is None
        assert summary.end_time is None
    
    def test_from_dict_with_datetime(self):
        """Test from_dict with datetime strings."""
        start_time_str = "2025-06-18T12:00:00.123456"
        end_time_str = "2025-06-18T12:02:15.654321"
        
        data = {
            'instance': 'test',
            'start_time': start_time_str,
            'end_time': end_time_str,
            'phases': []
        }
        
        summary = RunSummary.from_dict(data)
        
        assert summary.start_time == datetime.fromisoformat(start_time_str)
        assert summary.end_time == datetime.fromisoformat(end_time_str)
    
    def test_from_dict_with_phases(self):
        """Test from_dict with phase data."""
        data = {
            'instance': 'test',
            'phases': [
                {
                    'name': 'translation',
                    'command': ['my-translate', '--format', 'compact', 'test.param'],
                    'duration': 0.5,
                    'success': True,
                    'output_files': ['test.param'],
                    'error_message': '',
                    'peak_memory_kb': None,
                    'solver_stats': {}
                },
                {
                    'name': 'solving',
                    'command': ['savilerow', 'model.eprime'],
                    'duration': 12.0,
                    'success': True,
                    'output_files': ['solution.txt'],
                    'error_message': '',
                    'peak_memory_kb': 1024,
                    'solver_stats': {'horizon_number': 1}
                }
            ]
        }
        
        summary = RunSummary.from_dict(data)
        
        assert len(summary.phases) == 2
        assert summary.phases[0].name == "translation"
        assert summary.phases[0].command == ['my-translate', '--format', 'compact', 'test.param']
        assert summary.phases[1].name == "solving"
        assert summary.phases[1].solver_stats['horizon_number'] == 1
    
    def test_from_json(self):
        """Test JSON deserialization."""
        json_str = '''
        {
            "instance": "test",
            "model": "compact",
            "total_duration": 15.5,
            "phases": [
                {
                    "name": "solving",
                    "command": ["savilerow"],
                    "duration": 12.0,
                    "success": true,
                    "output_files": [],
                    "error_message": "",
                    "peak_memory_kb": null,
                    "solver_stats": {}
                }
            ]
        }
        '''
        
        summary = RunSummary.from_json(json_str)
        
        assert summary.instance == "test"
        assert summary.model == "compact"
        assert summary.total_duration == 15.5
        assert len(summary.phases) == 1
        assert summary.phases[0].name == "solving"
    
    def test_roundtrip_serialization(self):
        """Test full roundtrip serialization/deserialization."""
        original = RunSummary(
            instance="test",
            model="compact",
            solver="kissat",
            start_time=datetime(2025, 6, 18, 12, 0, 0),
            total_duration=15.5
        )
        
        phase = PhaseResult(
            name="solving",
            command=["savilerow", "model.eprime"],
            duration=12.0,
            success=True,
            solver_stats={'horizon_number': 1}
        )
        original.add_phase(phase)
        
        # Serialize to JSON and back
        json_str = original.to_json()
        reconstructed = RunSummary.from_json(json_str)
        
        assert reconstructed.instance == original.instance
        assert reconstructed.model == original.model
        assert reconstructed.solver == original.solver
        assert reconstructed.start_time == original.start_time
        assert reconstructed.total_duration == original.total_duration
        assert len(reconstructed.phases) == 1
        assert reconstructed.phases[0].name == "solving"
        assert reconstructed.phases[0].solver_stats['horizon_number'] == 1


class TestRunSummaryFileIO:
    """Test RunSummary file I/O operations."""
    
    def test_save_and_load(self):
        """Test saving and loading RunSummary to/from file."""
        summary = RunSummary(
            instance="test",
            model="compact",
            total_duration=15.5
        )
        
        phase = PhaseResult(name="solving", duration=12.0, success=True)
        summary.add_phase(phase)
        
        # The add_phase method adds to total_duration, so expect 15.5 + 12.0 = 27.5
        expected_total = 27.5
        
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_summary.json"
            
            # Save to file
            summary.save(file_path)
            assert file_path.exists()
            
            # Load from file
            loaded = RunSummary.load(file_path)
            
            assert loaded.instance == "test"
            assert loaded.model == "compact"
            assert loaded.total_duration == expected_total
            assert len(loaded.phases) == 1
            assert loaded.phases[0].name == "solving"
    


class TestUtilityFunctions:
    """Test utility functions for creating RunSummary and PhaseResult objects."""
    
    def test_create_run_summary(self):
        """Test create_run_summary utility function."""
        summary = create_run_summary("sample-instance", "demo", "kissat")

        assert summary.instance == "sample-instance"
        assert summary.model == "demo"
        assert summary.solver == "kissat"
        assert summary.timestamp != ""  # Should be set to current time
        assert summary.start_time is not None
    
    def test_create_phase_result(self):
        """Test create_phase_result utility function."""
        command = ["savilerow", "model.eprime", "problem.param"]
        phase = create_phase_result("solving", command)
        
        assert phase.name == "solving"
        assert phase.command == ["savilerow", "model.eprime", "problem.param"]
        assert phase.duration == 0.0
        assert phase.success is False
        
        # Ensure command is copied, not referenced
        command.append("extra")
        assert len(phase.command) == 3  # Should not be affected


class TestConsumerOwnedFields:
    """The ``extra`` bag: consumer fields the core does not interpret.

    The core owns the record's shape, but a consumer may have a datum that
    belongs on the run and that the core has no concept of. Declaring such a
    field here would grow a consumer's vocabulary into a domain-agnostic record;
    ``extra`` is where it goes instead.
    """

    def test_extra_is_flattened_into_the_saved_json(self):
        """On disk it is indistinguishable from a declared field.

        This is what makes moving a field into ``extra`` a no-op for every
        reader that indexes a saved summary by key.
        """
        s = RunSummary(instance="i", model="demo", solver="kissat")
        s.extra['replay_score'] = 1500

        data = s.to_dict()

        assert data['replay_score'] == 1500
        assert 'extra' not in data

    def test_empty_extra_adds_nothing(self):
        s = RunSummary(instance="i", model="demo", solver="kissat")
        assert 'extra' not in s.to_dict()

    def test_unknown_keys_are_gathered_rather_than_rejected(self):
        """A summary written by another consumer must load, not raise.

        Before ``extra``, from_dict was ``cls(**data)``, so any key the core did
        not declare raised TypeError - meaning one consumer could not read
        another's summary at all.
        """
        summary = RunSummary.from_dict({
            'instance': 'i',
            'model': 'demo',
            'replay_score': 1500,
            'some_other_consumers_field': 'whatever',
        })

        assert summary.instance == 'i'
        assert summary.extra['replay_score'] == 1500
        assert summary.extra['some_other_consumers_field'] == 'whatever'

    def test_extra_round_trips_through_json(self):
        s = RunSummary(instance="i", model="demo", solver="kissat")
        s.extra['replay_score'] = 1500

        back = RunSummary.from_json(s.to_json())

        assert back.extra['replay_score'] == 1500

    def test_a_core_field_wins_a_name_collision(self):
        """A consumer cannot shadow the record's own vocabulary."""
        s = RunSummary(instance="i", model="demo", solver="kissat")
        s.score = 100
        s.extra['score'] = 999

        assert s.to_dict()['score'] == 100

    def test_a_summary_written_before_the_field_moved_still_loads(self):
        """`canonical_score` used to be declared on RunSummary; it is a consumer
        concept (a replay of the solution, re-scored) that this package can
        neither compute nor interpret, so it moved into `extra`.

        Every `.summary` file already on disk carries it at the top level, and
        so does every summary a consumer writes today - the flattening means
        those are the same thing. What must not happen is the load raising.
        """
        summary = RunSummary.from_dict({
            'instance': '5x7-ps1-a13',
            'model': 'upf',
            'score': 1700,
            'canonical_score': 1500,
        })

        assert summary.score == 1700                        # core field
        assert summary.extra['canonical_score'] == 1500     # consumer field
        # And it goes back out where it came from.
        assert summary.to_dict()['canonical_score'] == 1500

    def test_phase_extra_flattens_and_gathers_too(self):
        """Phases carry the same contract (most phase extras belong in
        solver_stats, but the record must not reject an unknown phase key)."""
        s = RunSummary(instance="i", model="demo", solver="kissat")
        phase = PhaseResult(name="solving", duration=1.0, success=True)
        phase.extra['replayed'] = True
        s.phases.append(phase)

        data = s.to_dict()
        assert data['phases'][0]['replayed'] is True
        assert 'extra' not in data['phases'][0]

        back = RunSummary.from_dict(data)
        assert back.phases[0].extra['replayed'] is True
