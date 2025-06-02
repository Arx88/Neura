import unittest
from agent.run import detect_visualization_request # Adjust import based on actual file structure

class TestDetectVisualizationRequest(unittest.TestCase):

    def test_no_visualization_request(self):
        self.assertIsNone(detect_visualization_request("Hello, how are you?"))
        self.assertIsNone(detect_visualization_request("What is the capital of France?"))
        self.assertIsNone(detect_visualization_request("Explain quantum physics."))

    def test_bar_chart_requests(self):
        self.assertEqual(detect_visualization_request("Create a bar chart of sales data."), "bar_chart")
        self.assertEqual(detect_visualization_request("Can you make a grafico de barras?"), "bar_chart")
        self.assertEqual(detect_visualization_request("show me a bar graph"), "bar_chart")
        self.assertEqual(detect_visualization_request("Plot sales by region as bars."), "bar_chart")
        self.assertEqual(detect_visualization_request("I need a BAR chart for user engagement."), "bar_chart") # Case insensitivity

    def test_line_chart_requests(self):
        self.assertEqual(detect_visualization_request("Generate a line chart for stock prices."), "line_chart")
        self.assertEqual(detect_visualization_request("Un gráfico de líneas sobre el tiempo."), "line_chart")
        self.assertEqual(detect_visualization_request("Show temperature trends with a line graph."), "line_chart")
        self.assertEqual(detect_visualization_request("Plot population growth as a LINE graph."), "line_chart")

    def test_pie_chart_requests(self):
        self.assertEqual(detect_visualization_request("Make a pie chart of market share."), "pie_chart")
        self.assertEqual(detect_visualization_request("Un gráfico de pastel para la distribución."), "pie_chart")
        self.assertEqual(detect_visualization_request("Display budget allocation in a PIE chart."), "pie_chart")

    def test_histogram_requests(self):
        self.assertEqual(detect_visualization_request("Create a histogram of response times."), "histogram")
        self.assertEqual(detect_visualization_request("Genera un histograma de las edades."), "histogram")
        self.assertEqual(detect_visualization_request("HISTOGRAM of scores please."), "histogram")

    def test_generic_visualization_requests(self):
        self.assertEqual(detect_visualization_request("Make a plot of the data."), "generic_visualization")
        self.assertEqual(detect_visualization_request("Show me a visualizacion of these numbers."), "generic_visualization")
        self.assertEqual(detect_visualization_request("Can you graph this?"), "generic_visualization")
        self.assertEqual(detect_visualization_request("Create a diagram for this process flow data."), "generic_visualization")
        self.assertEqual(detect_visualization_request("I need a chart."), "generic_visualization") # Generic chart
        self.assertEqual(detect_visualization_request("Plot these values."), "generic_visualization")


    def test_mixed_requests(self):
        # If multiple specific keywords are present, current logic might pick one based on internal order.
        # E.g., "bar chart and line chart" -> might pick bar_chart if "bar" is checked before "line"
        # This is acceptable given the current implementation.
        self.assertEqual(detect_visualization_request("Make a bar chart and also a line graph."), "bar_chart") # "bar" comes before "line" in checks
        self.assertEqual(detect_visualization_request("I want a line graph, or maybe a pie chart."), "line_chart") # "line" comes before "pie"

    def test_case_insensitivity_general(self):
        self.assertEqual(detect_visualization_request("CREATE A BAR CHART OF SALES DATA."), "bar_chart")
        self.assertEqual(detect_visualization_request("gRaFiCo De BaRrAs"), "bar_chart")
        self.assertEqual(detect_visualization_request("ShOw Me A pLoT"), "generic_visualization")

    def test_empty_and_whitespace_strings(self):
        self.assertIsNone(detect_visualization_request(""))
        self.assertIsNone(detect_visualization_request("   "))

if __name__ == '__main__':
    unittest.main()
