# Data Visualization Feature

## 1. Overview

The Data Visualization feature enables Neura to generate and display actual graphical charts and plots in response to user requests. This is a significant enhancement over previous text-based or simulated chart representations, providing users with richer, more intuitive data insights. The system leverages Python libraries within a sandboxed environment to create these visualizations, which are then presented to the user through the frontend.

## 2. Components

The data visualization capability is comprised of several key components working together:

### `DataVisualizationTool` (`backend/agent/tools/visualization_tool.py`)

This class is the core agent tool responsible for handling all aspects of visualization generation and preparation for display.

*   **`create_bar_chart(title: str, categories: list, values: list, output_file: str, x_label: str = "", y_label: str = "")`**:
    *   Generates a bar chart using the `matplotlib` library within the agent's sandbox environment.
    *   The chart is saved as a PNG image file to the `/workspace/visualizations/` directory in the sandbox.
    *   Key parameters include the chart title, data categories (x-axis), corresponding values (y-axis), and the desired output filename (without extension). Optional labels for X and Y axes can also be provided.
*   **`view_visualization(image_path: str)`**:
    *   Checks for the existence of a specified visualization image file within the sandbox's `/workspace/` directory. This is useful for verifying if a chart was generated or if a requested image is available.
*   **`display_visualization_in_browser(image_path: str)`**:
    *   Takes the sandbox path of a generated visualization image (e.g., a PNG file from `create_bar_chart`).
    *   It reads this image, base64 encodes it, and then generates a self-contained HTML document. This HTML embeds the image directly using a `data:image/...;base64,...` URI.
    *   The primary output of this method, intended for the frontend, is `html_content` (the complete HTML string). It also returns `html_path` (the path where this HTML file itself is saved in the sandbox, e.g., `/workspace/visualizations/visualization_display_xxxx.html`) and `html_file_name`.

### Sandbox Setup (`backend/sandbox/sandbox.py`)

*   **`setup_visualization_environment(sandbox: Sandbox)`**:
    *   This function is automatically called when a new sandbox instance is created for the agent.
    *   It ensures that the necessary Python libraries for data visualization are installed in the sandbox environment. Currently, it installs `matplotlib`, `pandas`, `seaborn`, and `plotly` using `pip`.
    *   It also creates the `/workspace/visualizations` directory, which serves as the standard location for saving generated chart images and HTML files.

### Request Detection (`backend/agent/run.py`)

*   **`detect_visualization_request(request_text: str)`**:
    *   This utility function analyzes the user's natural language input to identify if they are asking for a chart or visualization.
    *   It looks for a predefined list of keywords (e.g., "chart", "graph", "plot", "gráfico", "barras", "líneas").
    *   Based on the keywords, it attempts to classify the requested chart type. Current identified types include:
        *   `bar_chart`
        *   `line_chart`
        *   `pie_chart`
        *   `histogram`
        *   `generic_visualization` (if a general plotting term is used)
    *   If no relevant keywords are found, it returns `None`. This detection helps the agent guide the LLM towards using the `DataVisualizationTool` when appropriate.

## 3. Workflow

The typical process flow for generating and displaying a visualization is as follows:

1.  **User Request**: The user sends a message to Neura that includes a request for a visualization (e.g., "Show me a bar chart of sales data for the last quarter").
2.  **Intent Detection**: The `detect_visualization_request` function in `backend/agent/run.py` processes the user's message. If visualization-related keywords are found, it identifies the likely intent and potentially the type of chart. This information may be used to augment the prompt for the LLM.
3.  **LLM Tool Selection**: The Language Model (LLM), now aware of the user's intent and the available `DataVisualizationTool`, decides to use this tool to fulfill the request.
4.  **Chart Generation**: The LLM invokes a method on the `DataVisualizationTool`, typically `create_bar_chart` (or other specific chart generation methods in the future). This method executes Python code within the sandbox, generating the visualization (e.g., a PNG image) and saving it to `/workspace/visualizations/`.
5.  **HTML Preparation**: Subsequently, the `display_visualization_in_browser` method is called (often by the LLM as a next step). It takes the path of the generated image, reads it, and creates a self-contained HTML file with the image embedded as a base64 data URI.
6.  **Frontend Rendering**: The `html_content` (the string containing the full HTML document) is sent back to the frontend as part of the agent's response (typically within a tool result). The frontend then renders this HTML content, usually within an `<iframe>` using the `srcdoc` attribute, making the visualization visible to the user.

## 4. Frontend Integration (Conceptual)

The recommended approach for displaying visualizations on the frontend is:

*   The frontend should monitor agent responses for tool results originating from the `DataVisualizationTool`, specifically the `display_visualization_in_browser` method.
*   When such a result is received, it will contain an `html_content` field. This field holds the complete, self-contained HTML string for the visualization.
*   The frontend should render this `html_content` string directly into an `<iframe>` element using its `srcdoc` attribute.
    ```html
    <iframe srcdoc="[html_content_from_tool_result]" sandbox="allow-scripts" title="Visualization"></iframe>
    ```
*   The `sandbox="allow-scripts"` attribute on the iframe may be necessary if the generated charts are interactive (e.g., from Plotly). For static images (like Matplotlib PNGs), fewer permissions might be needed, but `allow-scripts` provides flexibility for future interactive chart types.

## 5. Sandbox Lifecycle & Resource Management

To effectively manage resources and prevent excessive memory or quota consumption by Daytona sandboxes, several automated mechanisms are implemented throughout the sandbox lifecycle. While these processes are generally applicable to all sandboxes used by the agent, they are particularly relevant when considering features like data visualization that rely heavily on sandbox operations.

*   **Actions on Agent Run Completion**:
    *   When an agent run, processed by `backend/run_agent_background.py`, concludes (whether it completes successfully, fails, or is explicitly stopped by a user/system signal), several actions are taken:
        *   **Workspace Cleanup**: First, an automated cleanup process is initiated within the `/workspace` directory of the sandbox. This process attempts to delete common temporary files (e.g., `*.tmp`, `temp_*`, `*_temp.*`) and any empty directories. This helps to reclaim space and maintain a tidy environment before the sandbox is stopped.
        *   **Automatic Stopping**: Following the workspace cleanup, the associated Daytona sandbox is automatically sent a "stop" command. This action transitions the sandbox from a running state to a stopped state, conserving active resources and making it eligible for subsequent archival processes.

*   **Automated Archiving of Stopped Sandboxes**:
    *   The system includes utility scripts (e.g., `archive_inactive_sandboxes.py`, `archive_old_sandboxes.py`) that are typically run on a schedule.
    *   These scripts identify *stopped* sandboxes that have been inactive for a certain period or that have exceeded a defined age.
    *   Once identified, these sandboxes are moved to an "archived" state. Archiving further reduces resource consumption while still allowing for potential later retrieval if needed. The automatic stopping feature (mentioned above) significantly enhances the effectiveness of these archiving scripts by ensuring sandboxes are promptly made eligible for archival upon completion of their active use.

*   **Automated Deletion of Archived Sandboxes**:
    *   A new utility script, `backend/utils/scripts/delete_old_archived_sandboxes.py`, has been introduced to complete the sandbox lifecycle.
    *   **Purpose**: This script identifies and deletes sandboxes that have remained in the "archived" state for a configurable duration (default is typically 7 days, adjustable via the `--days-archived` argument).
    *   **Operation**: It iterates through all sandboxes, checks their state and the duration for which they have been archived, and then proceeds with deletion if the criteria are met.
    *   **Controls**: The script includes important operational controls:
        *   `--dry-run`: Allows administrators to preview which sandboxes *would be* deleted without actually performing any deletions.
        *   `--confirm`: Can be used to bypass interactive confirmation prompts, suitable for automated cron jobs or scripted execution.
    *   This script ensures that sandboxes do not remain in the archived state indefinitely, freeing up storage and other resources associated with Daytona.

*   **Lifecycle Summary**: Active Use -> Auto-Stopped (on agent run completion) -> Auto-Archived (by scheduled scripts based on inactivity/age) -> Auto-Deleted (by scheduled script based on time in archived state).

*   **Importance**: These automated lifecycle management mechanisms are crucial for maintaining the health of the Daytona environment, ensuring efficient resource utilization, and preventing the accumulation of unused sandboxes, thereby keeping operational costs and system load within manageable limits.

## 6. Future Enhancements

This feature lays the groundwork for more advanced data visualization capabilities. Potential future enhancements include:

*   **More Chart Types**: Adding dedicated methods for line charts, pie charts, scatter plots, heatmaps, etc., using libraries like `matplotlib`, `seaborn`, and `plotly`.
*   **Chart Customization**: Allowing users to specify colors, labels, themes, and other visual aspects of the charts.
*   **Interactive Charts**:
    *   Leveraging libraries like `Plotly` to generate fully interactive HTML/JavaScript charts that can be explored by the user (zooming, panning, hover-to-see-data). This would require ensuring the `iframe` sandbox attributes permit necessary script execution.
    *   Alternatively, sending Plotly JSON data to the frontend and using Plotly.js on the client-side for rendering, which could offer better performance and interactivity.
*   **Data Source Integration**: Connecting the visualization tools more directly with data retrieved by other tools (e.g., SQL query results, API data).
*   **Error Handling and Feedback**: Providing more granular feedback to the user if a chart cannot be generated due to data issues or unsupported requests.

This documentation provides a foundational understanding of the data visualization feature. As the system evolves, this document will be updated to reflect new capabilities and changes.
