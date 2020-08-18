# -*- coding: utf-8 -*-
import logging

# import pytest

from .utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
_safety_fixture, _safety_state, run_cell_ = make_safety_fixture()


def updated_symbol_names():
    return sorted(map(lambda sym: sym.readable_name, _safety_state[0].updated_symbols))


def run_cell(cell):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell)


def test_simplest():
    run_cell('a = 0')
    assert updated_symbol_names() == ['a']
    run_cell('a += 1')
    assert updated_symbol_names() == ['a']


@skipif_known_failing
def test_dict_hierarchy():
    run_cell('d = {}')
    assert updated_symbol_names() == ['d']
    run_cell('d["foo"] = {}')
    assert updated_symbol_names() == ['d[foo]', 'd']
    run_cell('d["foo"]["bar"] = []')
    assert updated_symbol_names() == ['d[foo][bar]', 'd[foo]', 'd']
    run_cell('d["foo"]["bar"].append(0)')
    assert updated_symbol_names() == ['d[foo][bar]', 'd[foo]', 'd']