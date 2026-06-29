# OneCompute Technical Financial Analysis

## Executive summary

OneCompute is designed to convert underutilized Microsoft-owned endpoint compute into a managed internal compute pool for delay-tolerant workloads. Based on the current project model, the Year 1 target case estimates approximately $125.6 million of Azure-equivalent compute capacity before incremental operating costs. After accounting for an estimated $8.0 million in incremental energy costs and $17.0 million in accelerated hardware depreciation, our model projects approximately $100.6 million in net Year 1 cost savings.

Over five years, assuming roughly 15 percent annual growth in recoverable compute value from device refreshes, broader participation, and more AI-capable hardware, the model projects approximately $678 million in cumulative net savings, which can be rounded to roughly $700 million for high-level pitch framing.

This analysis is intended as an order-of-magnitude financial model for the OneCompute hackathon project, not a final production business case.

## How we calculate the $125.6M Azure-equivalent compute value

The model splits our cost savings into the offset we are able to have from the cost we would normally spend on Azure equivalent on demand and spot compute.

| Compute source                | Annual compute value | Valuation basis                                                                                                              
| ----------------------------- | -------------------: | --------------------------------------------------------------------------------------------------------------------------- |
| Surface laptops, assigned     | **$47.3M**           |**200,000 laptops ×12 vCPU-equivalent cores × 3.0 recoverable hrs/day × 365 × $0.018/vCPU-hr spot-equivalent = ~$47.3M/year**|
| Unassigned Microsoft laptops  | **$55.0M**           |**10,000 laptops ×12 vCPU-equivalent cores × 24 recoverable hrs/day × 365× $0.052/vCPU-hr on-demand-equivalent=~$55.0M/year**|
| Xbox Series X devices         | **$0.3M**            |**200 Xbox devices -> 9.3M vCPU-hr × $0.01 ≈ $93k, GPU value: 1.23M GPU-hr × $0.15 ≈ $185k, total ≈ $0.3m/year**             |
| Idle dev boxes                | **$23.0M**           |**20,000 dev boxes × 16 vCPUs × 3.8 recoverable hrs/day × 365 × $0.052/vCPU-hr on-demand-equivalent = ~$23.0M/year**         |
| **Total gross compute value** | **$125.6M**          |**Sum of modeled Azure-equivalent recoverable compute value across all device classes.**                                     |

This target case keeps the largest employee laptop pool priced conservatively as spot-equivalent compute, while assigning more value to assets that are easier to control, schedule, and run for longer periods.

## Core model logic

OneCompute estimates savings by comparing recoverable endpoint compute capacity against equivalent Azure compute pricing. The model uses vCPU-hours as the primary proxy because it is simple to explain, maps cleanly to cloud pricing, and can be estimated across laptops, unassigned machines, dev boxes, and other devices.

The financial case is based on the idea that Microsoft already owns, powers, secures, and manages a large amount of device compute. If a portion of that capacity can be safely reclaimed without impacting employees, it can offset some internal cloud or batch workload demand.

## Year 1 target case

| Metric | Estimate | Commentary |
|---|---|---|
| Gross Azure-equivalent compute value | $125.6M | Target-case value of recoverable compute capacity benchmarked against comparable Azure usage |
| Incremental energy cost | ($8.0M) | Estimated additional electricity and runtime costs from using devices more actively |
| Accelerated depreciation | ($17.0M) | Possible estimated cost of faster hardware wear, replacement timing, and support burden |
| **Net Year 1 savings** | **$100.6M** | Target-case savings after operating and depreciation adjustments |

## Five-year projection

The projection assumes that net recoverable compute value grows by approximately 15 percent per year. This growth rate reflects a combination of newer AI-capable PCs, more powerful endpoint hardware, higher employee opt-in, more idle dev box capacity, and broader workload support.

| Year | Net savings estimate |
|---|---|
| Year 1 | $100.6M |
| Year 2 | $115.7M |
| Year 3 | $133.0M |
| Year 4 | $153.0M |
| Year 5 | $175.9M |
| **Five-year total** | **$678.3M** |

## Key assumptions
- We are not trying to estimate the theoretical performance of every device. Instead, we are estimating the amount of cloud compute that could be displaced. Azure, AWS, and most       enterprise infrastructure teams already measure and price compute in CPU and GPU consumption over time, making vCPU-hours and GPU-hours a natural financial proxy.
- Year 1 gross compute value is set at approximately $125.6 million of Azure-equivalent capacity.
- The $125.6 million value is derived from a target-case version of the original $101.6 million cost analysis, with higher assumed value for unassigned laptops and idle dev boxes.
- Year 1 incremental costs include $8.0 million for energy and $17.0 million for accelerated depreciation.
- Net Year 1 savings are estimated at approximately $100.6 million after costs.
- The five-year case assumes approximately 15 percent annual growth in net recoverable compute value.
- Growth is driven by higher device performance, more AI-capable endpoint hardware, broader opt-in participation, and improved scheduling efficiency.
- The model assumes workloads are delay-tolerant and can run safely in isolated environments without impacting employee productivity.
