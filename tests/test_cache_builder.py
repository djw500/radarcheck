from cache_builder import build_variable_query


def test_build_variable_query_single_param():
    config = {"nomads_params": ["var_REFC"], "level_params": []}
    result = build_variable_query(config)
    assert result == "var_REFC=on&"


def test_build_variable_query_with_levels():
    config = {
        "nomads_params": ["var_TMP"],
        "level_params": ["lev_2_m_above_ground=on"],
    }
    result = build_variable_query(config)
    assert "var_TMP=on" in result
    assert "lev_2_m_above_ground=on" in result
