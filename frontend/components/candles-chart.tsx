'use client'

import { createChart, IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

type Candle = {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

type Marker = {
  ts: string
  price: number
  color: string
  label: string
}

type Props = {
  candles: Candle[]
  lines?: Marker[]
}

export function CandlesChart({ candles, lines = [] }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!ref.current) return

    const chart: IChartApi = createChart(ref.current, {
      layout: {
        textColor: '#0f1720',
        background: { color: '#ffffff' }
      },
      rightPriceScale: {
        borderColor: '#d9e1e8'
      },
      timeScale: {
        borderColor: '#d9e1e8',
        timeVisible: true
      },
      grid: {
        vertLines: { color: '#edf1f4' },
        horzLines: { color: '#edf1f4' }
      },
      width: ref.current.clientWidth,
      height: 460
    })

    const series = chart.addCandlestickSeries({
      upColor: '#0f9d75',
      downColor: '#c7363e',
      borderVisible: false,
      wickUpColor: '#0f9d75',
      wickDownColor: '#c7363e'
    })

    series.setData(
      candles.map((c) => ({
        time: Math.floor(new Date(c.ts).getTime() / 1000) as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close
      }))
    )

    lines.forEach((line) => {
      const lineSeries: ISeriesApi<'Line'> = chart.addLineSeries({
        color: line.color,
        lineWidth: 2,
        priceLineVisible: true,
        lastValueVisible: false,
        title: line.label
      })
      lineSeries.setData(
        candles.map((c) => ({
          time: Math.floor(new Date(c.ts).getTime() / 1000) as UTCTimestamp,
          value: line.price
        }))
      )
    })

    chart.timeScale().fitContent()

    const observer = new ResizeObserver(() => {
      if (!ref.current) return
      chart.applyOptions({ width: ref.current.clientWidth })
    })
    observer.observe(ref.current)

    return () => {
      observer.disconnect()
      chart.remove()
    }
  }, [candles, lines])

  return <div ref={ref} className="w-full" />
}
