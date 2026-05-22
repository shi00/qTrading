"""ui/components/market_dashboard.py 单元测试"""

from unittest.mock import MagicMock, patch


from ui.components.market_dashboard import MarketDashboard


class TestMarketDashboardInit:
    def test_init_creates_controls(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()

            assert dashboard.sh_val is not None
            assert dashboard.sh_chg is not None
            assert dashboard.sz_val is not None
            assert dashboard.sz_chg is not None
            assert dashboard.cyb_val is not None
            assert dashboard.cyb_chg is not None
            assert dashboard.hsgt_val is not None
            assert dashboard.hsgt_sub is not None

    def test_init_creates_titles(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()

            assert dashboard.sh_title is not None
            assert dashboard.sz_title is not None
            assert dashboard.cyb_title is not None
            assert dashboard.hsgt_title is not None
            assert dashboard.concepts_title is not None

    def test_init_creates_concepts_row(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()

            assert dashboard.concepts_row is not None
            assert dashboard.concepts_placeholder is not None


class TestMarketDashboardUpdateData:
    def test_update_data_empty_data(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()
            dashboard.update_data({})

            assert dashboard._last_data == {}

    def test_update_data_none_data(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()
            dashboard.update_data(None)

            assert dashboard._last_data == {}

    def test_update_data_with_indices(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.TEXT_SECONDARY = "gray"

            dashboard = MarketDashboard()

            data = {
                "indices": [
                    {"value": "3000", "change": "+1.0%", "color": "RED"},
                    {"value": "10000", "change": "+0.5%", "color": "GREEN"},
                    {"value": "2000", "change": "-0.3%", "color": "GREY"},
                ]
            }

            dashboard.update_data(data)

            assert dashboard._last_data == data
            assert dashboard.sh_val.value == "3000"
            assert dashboard.sh_chg.value == "+1.0%"

    def test_update_data_with_hsgt(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.TEXT_SECONDARY = "gray"

            dashboard = MarketDashboard()

            data = {
                "hsgt": {
                    "value": "100亿",
                    "sub": "净流入",
                    "color": "RED",
                }
            }

            dashboard.update_data(data)

            assert dashboard.hsgt_val.value == "100亿"
            assert dashboard.hsgt_sub.value == "净流入"

    def test_update_data_with_hot_concepts(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"

            dashboard = MarketDashboard()

            data = {
                "hot_concepts": [
                    {"name": "AI", "change": "+5%", "color": "red"},
                    {"name": "芯片", "change": "-2%", "color": "green"},
                ]
            }

            dashboard.update_data(data)

            assert len(dashboard.concepts_row.controls) == 2

    def test_update_data_empty_hot_concepts_shows_placeholder(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()

            dashboard.update_data({"hot_concepts": []})

            assert dashboard.concepts_row.controls[0] == dashboard.concepts_placeholder

    def test_update_data_removes_excess_controls(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"

            dashboard = MarketDashboard()

            dashboard.update_data(
                {
                    "hot_concepts": [
                        {"name": "A", "change": "+1%", "color": "red"},
                        {"name": "B", "change": "+2%", "color": "red"},
                        {"name": "C", "change": "+3%", "color": "red"},
                    ]
                }
            )
            assert len(dashboard.concepts_row.controls) == 3

            dashboard.update_data(
                {
                    "hot_concepts": [
                        {"name": "A", "change": "+1%", "color": "red"},
                    ]
                }
            )
            assert len(dashboard.concepts_row.controls) == 1


class TestMarketDashboardUpdateTheme:
    def test_update_theme_without_last_data(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles") as mock_styles,
        ):
            mock_styles.dashboard_card.return_value = {
                "bgcolor": "white",
                "border": None,
                "shadow": None,
                "padding": 10,
                "border_radius": 8,
            }

            dashboard = MarketDashboard()
            dashboard.update_theme()

    def test_update_theme_with_last_data(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles") as mock_styles,
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.TEXT_SECONDARY = "gray"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"

            mock_styles.dashboard_card.return_value = {
                "bgcolor": "white",
                "border": None,
                "shadow": None,
                "padding": 10,
                "border_radius": 8,
            }

            dashboard = MarketDashboard()
            dashboard._last_data = {
                "indices": [
                    {"value": "3000", "change": "+1%", "color": "RED"},
                ]
            }

            dashboard.update_theme()


class TestMarketDashboardUpdateLocale:
    def test_update_locale(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="localized"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()
            dashboard.page = MagicMock()

            dashboard.update_locale()

            assert dashboard.sh_title.value == "localized"
            assert dashboard.sz_title.value == "localized"
            assert dashboard.cyb_title.value == "localized"
            assert dashboard.hsgt_title.value == "localized"
            assert dashboard.concepts_title.value == "localized"
            assert dashboard.concepts_placeholder.value == "localized"


class TestMarketDashboardUpdateIndexCard:
    def test_update_index_card_with_dict(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.TEXT_SECONDARY = "gray"

            dashboard = MarketDashboard()

            info = {"value": "3500", "change": "+1.5%", "color": "RED"}

            dashboard._update_index_card(dashboard.sh_val, dashboard.sh_chg, info)

            assert dashboard.sh_val.value == "3500"
            assert dashboard.sh_chg.value == "+1.5%"

    def test_update_index_card_with_non_dict(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"
            mock_colors.TEXT_SECONDARY = "gray"

            dashboard = MarketDashboard()

            dashboard._update_index_card(dashboard.sh_val, dashboard.sh_chg, "invalid")

            assert dashboard.sh_val.value == "--"


class TestMarketDashboardBuildConceptCard:
    def test_build_concept_card_skeleton(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.TEXT_PRIMARY = "black"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"

            dashboard = MarketDashboard()

            card = dashboard._build_concept_card_skeleton()

            assert card is not None
            assert card.data is not None
            assert "name" in card.data
            assert "icon" in card.data
            assert "change" in card.data

    def test_update_concept_card(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.TEXT_PRIMARY = "black"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"

            dashboard = MarketDashboard()

            card = dashboard._build_concept_card_skeleton()

            item = {"name": "AI概念", "change": "+5%", "color": "red"}

            dashboard._update_concept_card(card, item)

            refs = card.data
            assert refs["name"].value == "AI概念"
            assert refs["change"].value == "+5%"

    def test_update_concept_card_no_refs(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            dashboard = MarketDashboard()

            mock_container = MagicMock()
            mock_container.data = None

            dashboard._update_concept_card(mock_container, {"name": "test"})

    def test_build_concept_card(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors") as mock_colors,
            patch("ui.components.market_dashboard.AppStyles"),
        ):
            mock_colors.TEXT_PRIMARY = "black"
            mock_colors.SURFACE = "white"
            mock_colors.BORDER = "gray"
            mock_colors.UP = "red"
            mock_colors.DOWN = "green"

            dashboard = MarketDashboard()

            item = {"name": "新能源", "change": "+3%", "color": "red"}

            card = dashboard._build_concept_card(item)

            assert card is not None
            refs = card.data
            assert refs["name"].value == "新能源"


class TestMarketDashboardBuildCard:
    def test_build_card(self):
        with (
            patch("ui.components.market_dashboard.I18n.get", return_value="test"),
            patch("ui.components.market_dashboard.AppColors"),
            patch("ui.components.market_dashboard.AppStyles") as mock_styles,
        ):
            mock_styles.dashboard_card.return_value = {
                "bgcolor": "white",
                "border": None,
                "shadow": None,
                "padding": 10,
                "border_radius": 8,
            }

            dashboard = MarketDashboard()

            title_ctrl = MagicMock()
            control1 = MagicMock()
            control2 = MagicMock()

            card = dashboard._build_card(title_ctrl, control1, control2)

            assert card is not None
