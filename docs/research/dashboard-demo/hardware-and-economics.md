# Hardware and economics

## Hardware trend to pitch

Microsoft defines Copilot+ PCs as Windows 11 devices with NPUs capable of more than 40 TOPS (https://learn.microsoft.com/en-us/windows/ai/npu-devices/), and introduced the category as a CPU+GPU+NPU system architecture for local AI (https://blogs.microsoft.com/blog/2024/05/20/introducing-copilot-pcs/). AMD's Ryzen AI 300 press materials cite up to 50 NPU TOPS (https://www.amd.com/en/newsroom/press-releases/2024-6-2-amd-unveils-next-gen-zen-5-ryzen-processors-to-p.html). Intel says Core Ultra 200V reaches up to 120 platform TOPS and emphasizes its fourth-generation NPU (https://www.intc.com/news-events/press-releases/detail/1707/new-core-ultra-processors-deliver-breakthrough-performance). NVIDIA's RTX 50 laptop comparison page lists 440 to 1,824 AI TOPS across the laptop stack (https://www.nvidia.com/en-us/geforce/laptops/compare/).

## Desk-side super-node trend

NVIDIA DGX Station for Windows is announced with up to 20 PFLOPS FP4 and up to 748 GB coherent memory for local trillion-parameter-class workflows (https://nvidianews.nvidia.com/news/nvidia-dgx-station-for-windows-puts-a-trillion-parameter-ai-supercomputer-on-every-enterprise-desk). NVIDIA RTX Spark is announced with 1 PFLOP AI compute and up to 128 GB unified memory for Windows agent PCs (https://nvidianews.nvidia.com/news/nvidia-microsoft-windows-pcs-agents-rtx-spark). Treat these as roadmap/super-node proof points, not PoC hardware assumptions.

## Economics framing

Azure NCads H100 v5 is explicitly positioned for real-world Applied AI and batch inferencing, with 1-2 NVIDIA H100 NVL GPUs with 94 GB memory each (https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series). The Azure Retail Prices API is the official unauthenticated source for retail rates (https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices); queried for East US `Standard_NC40ads_H100_v5`, it returned $6.98/hr Linux and $8.82/hr Windows on-demand meters (https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Virtual%20Machines%27%20and%20armSkuName%20eq%20%27Standard_NC40ads_H100_v5%27%20and%20armRegionName%20eq%20%27eastus%27%20and%20priceType%20eq%20%27Consumption%27).

OpenAI Batch advertises 50% lower cost, higher rate limits, and 24-hour turnaround for asynchronous work (https://developers.openai.com/api/docs/guides/batch). Anthropic Message Batches likewise state 50% standard-price usage, large-volume asynchronous processing, and most batches finishing in less than 1 hour with 24-hour expiry (https://platform.claude.com/docs/en/build-with-claude/batch-processing).

## Pitch-safe ROI line

"NightShift targets the same class of delay-tolerant work cloud providers discount: evals, batch prompts, embeddings, synthetic data, transforms, render chunks, and test fan-out. We only count savings when a measured accepted unit displaced a real paid cloud unit."
