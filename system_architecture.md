flowchart LR

    %% External
    User((User))

    subgraph System["AI-IoT Platform"]
        direction TB

        LoRa["LoRa Bridge"]

        LLM["LLM"]
        Agent["AI Agent"]

        MCP["MCP Server"]

        Rule["Rule Engine"]
        ML["ML Engine"]
        Notify["Notifier"]

        Recorder["Recorder"]

        DB[(Local DB)]

        %% Agent Layer
        LLM <--> Agent
        Agent --> MCP

        %% MCP Services
        MCP --> Rule
        MCP --> ML
        MCP --> Notify

        %% Data Layer
        Rule --> Recorder
        ML --> Recorder
        Notify --> Recorder

        Recorder --> DB

        %% LoRa Integration
        LoRa --> Recorder
        MCP --> LoRa
    end

    %% External interactions
    User --> Agent
    LoRa --> User
    Notify --> User