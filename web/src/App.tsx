import { useState, useEffect, useRef } from "react";
import {
  ArrowRight,
  Plug,
  Sparkles,
  PaintBucket,
  LineChart,
  Building2,
  Dumbbell,
  Users,
  Heart,
  Globe,
  ShoppingBag,
  DollarSign,
  Mail,
  Cpu,
  Smartphone,
  CheckCircle2,
  Trophy,
  Activity,
  Zap,
  ShieldCheck,
} from "lucide-react";

const BOOK_A_CALL_URL = "mailto:hello@tr3d.co?subject=Book%20a%20call";

// ==========================================
// 1. AETHER FLOW BACKGROUND (Canvas Animation)
// ==========================================
const AetherFlowBackground = () => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animationFrameId: number;
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    };
    window.addEventListener("resize", handleResize);

    const particles: Array<{
      x: number;
      y: number;
      radius: number;
      vx: number;
      vy: number;
      alpha: number;
      color: string;
    }> = [];

    const colors = ["#ff5500", "#ffaa00", "#3388ff", "#8833ff"];

    for (let i = 0; i < 45; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        radius: Math.random() * 120 + 80,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        alpha: Math.random() * 0.15 + 0.05,
        color: colors[Math.floor(Math.random() * colors.length)],
      });
    }

    const render = () => {
      ctx.clearRect(0, 0, width, height);

      particles.forEach((p) => {
        p.x += p.vx;
        p.y += p.vy;

        if (p.x < -p.radius) p.x = width + p.radius;
        if (p.x > width + p.radius) p.x = -p.radius;
        if (p.y < -p.radius) p.y = height + p.radius;
        if (p.y > height + p.radius) p.y = -p.radius;

        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius);
        gradient.addColorStop(0, p.color);
        gradient.addColorStop(1, "transparent");

        ctx.globalAlpha = p.alpha;
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fill();
      });

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      window.removeEventListener("resize", handleResize);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-0 bg-zinc-950"
    />
  );
};

// ==========================================
// 2. UI COMPONENTS & HEADER / HERO
// ==========================================
const Button = ({
  children,
  variant = "default",
  size = "md",
  className = "",
  asChild = false,
  onClick,
  href,
}: any) => {
  const baseStyles =
    "inline-flex items-center justify-center font-medium rounded-lg transition-all focus:outline-none focus:ring-2 focus:ring-amber-500/50 disabled:opacity-50";

  const variants: Record<string, string> = {
    default: "bg-amber-500 text-zinc-950 hover:bg-amber-400 font-semibold shadow-lg shadow-amber-500/20",
    outline: "border border-zinc-700 bg-zinc-900/50 text-zinc-100 hover:bg-zinc-800 backdrop-blur",
    ghost: "text-zinc-300 hover:text-white hover:bg-zinc-800/60",
  };

  const sizes: Record<string, string> = {
    sm: "px-3 py-1.5 text-xs",
    md: "px-4 py-2 text-sm",
    lg: "px-6 py-3 text-base",
  };

  const combinedClass = `${baseStyles} ${variants[variant] || variants.default} ${sizes[size] || sizes.md} ${className}`;

  if (asChild && href) {
    return (
      <a href={href} className={combinedClass}>
        {children}
      </a>
    );
  }

  return (
    <button onClick={onClick} className={combinedClass}>
      {children}
    </button>
  );
};

const Wordmark = () => (
  <a href="#top" className="flex items-center gap-2.5 font-bold tracking-tight text-white text-lg">
    <span className="grid h-8 w-8 place-items-center rounded-lg bg-amber-500 text-zinc-950 font-black text-sm shadow-md shadow-amber-500/30">
      <span className="block h-2.5 w-2.5 rounded-sm bg-zinc-950" />
    </span>
    <span className="font-mono tracking-wider font-extrabold text-xl">TR3D</span>
  </a>
);

const Nav = () => (
  <header className="sticky top-0 z-50 border-b border-zinc-800/60 bg-zinc-950/70 backdrop-blur-md">
    <div className="container mx-auto flex h-16 items-center justify-between px-4 md:px-8">
      <Wordmark />
      <nav className="hidden items-center gap-8 text-sm text-zinc-400 font-medium md:flex">
        <a href="#different" className="hover:text-amber-400 transition-colors">The Difference</a>
        <a href="#how" className="hover:text-amber-400 transition-colors">How it works</a>
        <a href="#services" className="hover:text-amber-400 transition-colors">The Engine</a>
        <a href="#monetize" className="hover:text-amber-400 transition-colors">Monetize</a>
        <a href="#demo" className="hover:text-amber-400 transition-colors">Live Demo</a>
      </nav>
      <div className="flex items-center gap-3">
        <Button asChild variant="default" size="sm" href={BOOK_A_CALL_URL}>
          Book a Demo <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  </header>
);

const Hero = () => (
  <section id="top" className="relative overflow-hidden pt-12 pb-20 md:pt-24 md:pb-32">
    <div className="container relative z-10 mx-auto px-4 md:px-8">
      <div className="mx-auto max-w-3xl text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-3.5 py-1 text-xs font-semibold text-amber-400 backdrop-blur mb-6">
          <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
          The White-Label Treadmill Coaching Engine
        </span>
        <h1 className="font-display text-4xl font-extrabold leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
          From the treadmill to the{" "}
          <span className="relative inline-block text-amber-400">
            <span className="relative z-10">finish line</span>
            <span className="absolute inset-x-0 bottom-1.5 -z-0 h-3 bg-amber-500/20 md:h-4 rounded" />
          </span>
          .
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-base text-zinc-300 md:text-lg leading-relaxed font-light">
          The world's first white-label running coach built specifically for indoor treadmills — not GPS watches.
          Your app, your brand, powered by an elite coaching engine that trains members for real,
          iconic races. Scalable, affordable, fully customized — pure sports science, zero hallucinated AI.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <Button asChild variant="default" size="lg" href={BOOK_A_CALL_URL}>
            Get Engine API Key
          </Button>
          <Button asChild variant="outline" size="lg" href="#how">
            See How It Works
          </Button>
        </div>

        {/* Hero KPI Stats Bar */}
        <div className="mt-16 grid grid-cols-2 gap-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 backdrop-blur p-6 sm:grid-cols-4 text-left">
          <div className="border-r border-zinc-800/80 pr-4 last:border-0">
            <div className="text-2xl font-bold text-white font-mono">100%</div>
            <div className="text-xs text-zinc-400">Treadmill Telemetry Native</div>
          </div>
          <div className="border-r border-zinc-800/80 pr-4 last:border-0">
            <div className="text-2xl font-bold text-amber-400 font-mono">50+</div>
            <div className="text-xs text-zinc-400">Simulated World Major Races</div>
          </div>
          <div className="border-r border-zinc-800/80 pr-4 last:border-0">
            <div className="text-2xl font-bold text-white font-mono">0%</div>
            <div className="text-xs text-zinc-400">AI Hallucination Risk</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-amber-400 font-mono">&lt; 1 Day</div>
            <div className="text-xs text-zinc-400">API Integration Time</div>
          </div>
        </div>
      </div>
    </div>
  </section>
);

// ==========================================
// 3. FEATURE SECTIONS
// ==========================================

const Different = () => (
  <section id="different" className="py-20 border-t border-zinc-800/50 bg-zinc-950/40 backdrop-blur">
    <div className="container mx-auto px-4 md:px-8">
      <div className="text-center max-w-2xl mx-auto mb-16">
        <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">The Paradigm Shift</h2>
        <h3 className="text-3xl md:text-4xl font-bold text-white">Why Outdoor Fitness Apps Fail Indoors</h3>
        <p className="text-zinc-400 mt-3 text-sm md:text-base">
          Standard fitness apps rely on outdoor GPS signals or generic, static treadmill videos. TR3D treats the treadmill as a dynamic race simulator.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/30 opacity-70">
          <div className="h-10 w-10 rounded-lg bg-zinc-800 flex items-center justify-center text-zinc-400 mb-4">
            <Smartphone className="h-5 w-5" />
          </div>
          <h4 className="text-lg font-bold text-zinc-300">GPS Outdoor Apps</h4>
          <p className="text-sm text-zinc-400 mt-2">
            Rely entirely on outdoor pace & GPS signals. When brought indoors, calorie burn, cadence, and slope adaptation become inaccurate guesses.
          </p>
        </div>

        <div className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/30 opacity-70">
          <div className="h-10 w-10 rounded-lg bg-zinc-800 flex items-center justify-center text-zinc-400 mb-4">
            <Dumbbell className="h-5 w-5" />
          </div>
          <h4 className="text-lg font-bold text-zinc-300">Generic Treadmill Content</h4>
          <p className="text-sm text-zinc-400 mt-2">
            Boring manual speed presets or non-interactive video streams. Users zone out and fail to build real race stamina or elevation adaptation.
          </p>
        </div>

        <div className="p-6 rounded-xl border-2 border-amber-500/50 bg-amber-500/5 relative shadow-xl shadow-amber-500/5">
          <span className="absolute -top-3 right-4 bg-amber-500 text-zinc-950 text-[10px] font-bold px-2 py-0.5 rounded uppercase font-mono">
            TR3D Engine
          </span>
          <div className="h-10 w-10 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center mb-4">
            <Zap className="h-5 w-5" />
          </div>
          <h4 className="text-lg font-bold text-white">Interactive Race Telemetry</h4>
          <p className="text-sm text-zinc-300 mt-2">
            Simulates real race course profiles (Boston, NYC, London). Automatically calculates speed, incline shifts, dynamic energy zones, and fatigue science in real-time.
          </p>
        </div>
      </div>
    </div>
  </section>
);

const HowItWorks = () => {
  const steps = [
    {
      icon: Plug,
      number: "01",
      title: "Embed the SDK/API",
      desc: "Connect our lightweight React/Native API into your app within hours. Fully backend-managed.",
    },
    {
      icon: PaintBucket,
      number: "02",
      title: "Custom Brand Rules",
      desc: "Apply your brand styling, custom physiological zones, and tailored audio/visual cues.",
    },
    {
      icon: Activity,
      number: "03",
      title: "Treadmill Telemetry Sync",
      desc: "Stream speed, incline, and HR telemetry directly into the deterministic TR3D engine.",
    },
    {
      icon: Trophy,
      number: "04",
      title: "Deliver Race Ready Athletes",
      desc: "Members prepare indoors for real-world marathon and 10K courses with sports-science precision.",
    },
  ];

  return (
    <section id="how" className="py-20 border-t border-zinc-800/50">
      <div className="container mx-auto px-4 md:px-8">
        <div className="text-center max-w-2xl mx-auto mb-16">
          <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">Implementation</h2>
          <h3 className="text-3xl md:text-4xl font-bold text-white">How TR3D Integrates Into Your App</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {steps.map((step, idx) => (
            <div key={idx} className="relative p-6 rounded-xl border border-zinc-800/80 bg-zinc-900/50 backdrop-blur group hover:border-amber-500/40 transition-colors">
              <span className="text-3xl font-black font-mono text-zinc-700 group-hover:text-amber-500/40 transition-colors block mb-4">
                {step.number}
              </span>
              <step.icon className="h-8 w-8 text-amber-400 mb-3" />
              <h4 className="text-lg font-bold text-white mb-2">{step.title}</h4>
              <p className="text-xs text-zinc-400 leading-relaxed">{step.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

const WhyNow = () => (
  <section id="whynow" className="py-20 bg-gradient-to-b from-zinc-950/80 via-zinc-900/30 to-zinc-950 border-t border-zinc-800/50">
    <div className="container mx-auto px-4 md:px-8">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
        <div>
          <span className="text-xs font-mono uppercase tracking-widest text-amber-400 font-semibold">Market Dynamics</span>
          <h2 className="text-3xl md:text-5xl font-extrabold text-white mt-2 leading-tight">
            Indoor Running is Exploding. Content Sucks.
          </h2>
          <p className="text-zinc-300 mt-4 leading-relaxed font-light">
            More runners train on treadmills than ever before due to extreme weather, safety, and busy schedules. Yet, most fitness hardware & app providers offer static workouts from 2012.
          </p>

          <div className="mt-8 space-y-4">
            <div className="flex gap-4 items-start">
              <div className="mt-1 h-6 w-6 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center shrink-0">
                <CheckCircle2 className="h-4 w-4" />
              </div>
              <div>
                <h4 className="text-white font-semibold">Sell-Out World Majors</h4>
                <p className="text-xs text-zinc-400">Race signups are at record highs. Runners want course-specific elevation profile training.</p>
              </div>
            </div>

            <div className="flex gap-4 items-start">
              <div className="mt-1 h-6 w-6 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center shrink-0">
                <CheckCircle2 className="h-4 w-4" />
              </div>
              <div>
                <h4 className="text-white font-semibold">White-Label Freedom</h4>
                <p className="text-xs text-zinc-400">Don't push your users to 3rd-party apps. Keep engagement and subscription revenues inside your own platform ecosystem.</p>
              </div>
            </div>
          </div>
        </div>

        <div className="p-8 rounded-2xl border border-zinc-800 bg-zinc-900/60 backdrop-blur relative overflow-hidden">
          <div className="absolute top-0 right-0 p-8 opacity-10">
            <Globe className="h-48 w-48 text-white" />
          </div>
          <h3 className="text-lg font-bold text-white mb-6">Market Trends Overview</h3>
          <div className="space-y-6">
            <div>
              <div className="flex justify-between text-xs font-mono text-zinc-400 mb-2">
                <span>Treadmill Runners Seeking Structured Plans</span>
                <span className="text-amber-400 font-bold">84%</span>
              </div>
              <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
                <div className="h-full bg-amber-500 w-[84%]" />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-xs font-mono text-zinc-400 mb-2">
                <span>Apps Missing Race Simulation Tools</span>
                <span className="text-amber-400 font-bold">92%</span>
              </div>
              <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
                <div className="h-full bg-amber-400 w-[92%]" />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-xs font-mono text-zinc-400 mb-2">
                <span>Partner LTV Increase With Custom Coaching</span>
                <span className="text-amber-400 font-bold">+3.2x</span>
              </div>
              <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
                <div className="h-full bg-emerald-500 w-[75%]" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
);

const Services = () => {
  const features = [
    {
      icon: Cpu,
      title: "Deterministic Engine",
      desc: "Built on rigorous exercise physiology formulas. Zero AI hallucinations, 100% repeatable sports science.",
    },
    {
      icon: LineChart,
      title: "Elevation & Incline Sync",
      desc: "Calculates real hill grade impacts on treadmill speed to simulate iconic outdoor marathon topographies.",
    },
    {
      icon: Heart,
      title: "Heart-Rate Zone Calibration",
      desc: "Dynamically adapts targets to member heart-rate zones, preventing burnout and overtraining.",
    },
    {
      icon: Sparkles,
      title: "White-Label Audio / Cues",
      desc: "Inject your brand's voice, coaching audio prompts, or custom workout messaging triggers.",
    },
    {
      icon: Plug,
      title: "Cross-Hardware Compatible",
      desc: "Works seamlessly across Bluetooth FTMS, gym treadmill consoles, or standalone mobile sensors.",
    },
    {
      icon: ShieldCheck,
      title: "Enterprise Grade Scalability",
      desc: "Low-latency REST/WebSocket endpoints built for millions of concurrent running sessions.",
    },
  ];

  return (
    <section id="services" className="py-20 border-t border-zinc-800/50">
      <div className="container mx-auto px-4 md:px-8">
        <div className="text-center max-w-2xl mx-auto mb-16">
          <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">The Engine Capabilities</h2>
          <h3 className="text-3xl md:text-4xl font-bold text-white">Built For Developers, Crafted For Athletes</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((item, idx) => (
            <div key={idx} className="p-6 rounded-xl border border-zinc-800/80 bg-zinc-900/40 hover:bg-zinc-900/70 backdrop-blur transition-all">
              <item.icon className="h-7 w-7 text-amber-400 mb-4" />
              <h4 className="text-base font-bold text-white mb-2">{item.title}</h4>
              <p className="text-xs text-zinc-400 leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

const Monetize = () => (
  <section id="monetize" className="py-20 bg-zinc-900/20 border-t border-zinc-800/50">
    <div className="container mx-auto px-4 md:px-8">
      <div className="text-center max-w-2xl mx-auto mb-16">
        <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">Monetization Models</h2>
        <h3 className="text-3xl md:text-4xl font-bold text-white">Unlock New Revenue Streams</h3>
        <p className="text-zinc-400 mt-2 text-sm">
          Turn passive treadmill users into high-margin digital subscribers.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-8 rounded-xl border border-zinc-800 bg-zinc-900/50 flex flex-col justify-between">
          <div>
            <DollarSign className="h-8 w-8 text-amber-400 mb-4" />
            <h4 className="text-xl font-bold text-white">Premium Marathon Tier</h4>
            <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
              Charge members a monthly add-on ($9.99 - $19.99/mo) for custom 16-week treadmill marathon & half-marathon race preparation plans.
            </p>
          </div>
          <div className="mt-6 pt-4 border-t border-zinc-800 text-xs font-mono text-amber-400">
            High Conversion Upsell
          </div>
        </div>

        <div className="p-8 rounded-xl border border-zinc-800 bg-zinc-900/50 flex flex-col justify-between">
          <div>
            <Building2 className="h-8 w-8 text-amber-400 mb-4" />
            <h4 className="text-xl font-bold text-white">Gym Chain Integration</h4>
            <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
              Equip your gym treadmills with race-mode software. Drive digital engagement, retain members, and differentiate your club floor.
            </p>
          </div>
          <div className="mt-6 pt-4 border-t border-zinc-800 text-xs font-mono text-amber-400">
            B2B Enterprise License
          </div>
        </div>

        <div className="p-8 rounded-xl border border-zinc-800 bg-zinc-900/50 flex flex-col justify-between">
          <div>
            <ShoppingBag className="h-8 w-8 text-amber-400 mb-4" />
            <h4 className="text-xl font-bold text-white">Brand Sponsorships</h4>
            <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
              Partner with shoe brands or race organizers to host virtual indoor races on famous routes with real brand activations.
            </p>
          </div>
          <div className="mt-6 pt-4 border-t border-zinc-800 text-xs font-mono text-amber-400">
            Sponsorship Revenue
          </div>
        </div>
      </div>
    </div>
  </section>
);

const Audience = () => {
  const audiences = [
    { title: "Connected Hardware OEMs", icon: Smartphone, desc: "Upgrade default console software with world-class coaching." },
    { title: "Commercial Gym Chains", icon: Building2, desc: "Offer connected virtual running programs without building backend tech." },
    { title: "Boutique Fitness Studios", icon: Dumbbell, desc: "Add structured treadmill running tracks to complement strength classes." },
    { title: "Digital Health & Fitness Apps", icon: Users, desc: "Instantly expand your app feature set to indoor runners." },
  ];

  return (
    <section id="audience" className="py-20 border-t border-zinc-800/50">
      <div className="container mx-auto px-4 md:px-8">
        <div className="text-center max-w-2xl mx-auto mb-16">
          <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">Target Audience</h2>
          <h3 className="text-3xl md:text-4xl font-bold text-white">Who Uses TR3D Engine?</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {audiences.map((aud, idx) => (
            <div key={idx} className="p-6 rounded-xl border border-zinc-800 bg-zinc-900/30 text-center flex flex-col items-center">
              <div className="h-12 w-12 rounded-full bg-amber-500/10 text-amber-400 flex items-center justify-center mb-4">
                <aud.icon className="h-6 w-6" />
              </div>
              <h4 className="text-base font-bold text-white mb-2">{aud.title}</h4>
              <p className="text-xs text-zinc-400 leading-relaxed">{aud.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

const Why = () => (
  <section id="why" className="py-20 border-t border-zinc-800/50 bg-zinc-950/60">
    <div className="container mx-auto px-4 md:px-8">
      <div className="max-w-3xl mx-auto text-center">
        <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">Why TR3D</h2>
        <h3 className="text-3xl md:text-4xl font-bold text-white mb-6">Deterministic Sports Science Over AI Hype</h3>
        <p className="text-zinc-300 text-base leading-relaxed font-light">
          Unlike generic LLMs that hallucinate workout recommendations or dangerous pacing plans, TR3D is built on strict exercise physiology rules. Every workout adaptively updates based on actual physiological responses (HR, speed, duration, slope) giving your runners guaranteed safety and maximal training response.
        </p>

        <div className="mt-10 flex flex-wrap justify-center gap-8 text-left max-w-xl mx-auto">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="h-5 w-5 text-amber-400" />
            <span className="text-sm font-medium text-zinc-200">Zero AI Hallucination</span>
          </div>
          <div className="flex items-center gap-3">
            <CheckCircle2 className="h-5 w-5 text-amber-400" />
            <span className="text-sm font-medium text-zinc-200">ISO Physiology Validated</span>
          </div>
          <div className="flex items-center gap-3">
            <CheckCircle2 className="h-5 w-5 text-amber-400" />
            <span className="text-sm font-medium text-zinc-200">Ultra-low Latency Sync</span>
          </div>
        </div>
      </div>
    </div>
  </section>
);

// ==========================================
// 4. INTERACTIVE LIVE DEMO WIDGET
// ==========================================
const Demo = () => {
  const [selectedRace, setSelectedRace] = useState("boston");
  const [speed, setSpeed] = useState(6.5); // mph

  const races: Record<string, { name: string; mile: string; elevation: string; targetPace: string; incline: number }> = {
    boston: { name: "Boston Marathon", mile: "Mile 20 (Heartbreak Hill)", elevation: "+4.5%", targetPace: "08:15 /mi", incline: 4.5 },
    nyc: { name: "NYC Marathon", mile: "Mile 15 (Queensboro Bridge)", elevation: "+3.0%", targetPace: "08:30 /mi", incline: 3.0 },
    london: { name: "London Marathon", mile: "Mile 22 (Embankment Flat)", elevation: "0.0%", targetPace: "07:45 /mi", incline: 0.0 },
  };

  const current = races[selectedRace];

  // Calculated Heart Rate simulation
  const simulatedHR = Math.min(185, Math.round(135 + (speed - 5) * 12 + current.incline * 4));
  const estimatedCalories = Math.round(speed * 95 + current.incline * 20);

  return (
    <section id="demo" className="py-20 border-t border-zinc-800/50 bg-zinc-950">
      <div className="container mx-auto px-4 md:px-8">
        <div className="text-center max-w-2xl mx-auto mb-12">
          <h2 className="text-xs uppercase tracking-widest font-mono text-amber-400 mb-2 font-semibold">Interactive Preview</h2>
          <h3 className="text-3xl md:text-4xl font-bold text-white">Experience The Telemetry Engine</h3>
          <p className="text-xs text-zinc-400 mt-2">
            Select a world race route and adjust treadmill speed to observe real-time telemetry calculation.
          </p>
        </div>

        <div className="max-w-4xl mx-auto rounded-2xl border border-zinc-800 bg-zinc-900/80 p-6 md:p-8 backdrop-blur shadow-2xl">
          {/* Race Selector Buttons */}
          <div className="flex flex-wrap gap-2 mb-6 border-b border-zinc-800 pb-4">
            <button
              onClick={() => setSelectedRace("boston")}
              className={`px-4 py-2 rounded-lg text-xs font-mono font-semibold transition-all ${
                selectedRace === "boston" ? "bg-amber-500 text-zinc-950" : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              Boston Marathon
            </button>
            <button
              onClick={() => setSelectedRace("nyc")}
              className={`px-4 py-2 rounded-lg text-xs font-mono font-semibold transition-all ${
                selectedRace === "nyc" ? "bg-amber-500 text-zinc-950" : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              NYC Marathon
            </button>
            <button
              onClick={() => setSelectedRace("london")}
              className={`px-4 py-2 rounded-lg text-xs font-mono font-semibold transition-all ${
                selectedRace === "london" ? "bg-amber-500 text-zinc-950" : "bg-zinc-800 text-zinc-400 hover:text-white"
              }`}
            >
              London Marathon
            </button>
          </div>

          {/* Telemetry Display */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div className="p-4 rounded-xl bg-zinc-950 border border-zinc-800">
              <span className="text-[10px] uppercase font-mono text-zinc-500">Current Segment</span>
              <div className="text-lg font-bold text-white mt-1">{current.name}</div>
              <div className="text-xs text-amber-400 mt-1 font-mono">{current.mile}</div>
            </div>

            <div className="p-4 rounded-xl bg-zinc-950 border border-zinc-800">
              <span className="text-[10px] uppercase font-mono text-zinc-500">Simulated Elevation Grade</span>
              <div className="text-2xl font-black font-mono text-white mt-1">{current.elevation}</div>
              <div className="text-xs text-zinc-400 mt-1">Auto-adjusted incline</div>
            </div>

            <div className="p-4 rounded-xl bg-zinc-950 border border-zinc-800">
              <span className="text-[10px] uppercase font-mono text-zinc-500">Calculated Heart Rate</span>
              <div className="flex items-center gap-2 mt-1">
                <Heart className="h-5 w-5 text-red-500 animate-pulse" />
                <span className="text-2xl font-black font-mono text-white">{simulatedHR} <span className="text-xs font-normal text-zinc-400">BPM</span></span>
              </div>
              <div className="text-xs text-zinc-400 mt-1">Zone {simulatedHR > 165 ? "4 (Threshold)" : "3 (Aerobic)"}</div>
            </div>
          </div>

          {/* Speed Controls */}
          <div className="p-6 rounded-xl bg-zinc-950/60 border border-zinc-800 space-y-4">
            <div className="flex justify-between items-center">
              <label className="text-xs font-mono text-zinc-300">Treadmill Speed Control (MPH)</label>
              <span className="text-lg font-mono font-bold text-amber-400">{speed.toFixed(1)} MPH</span>
            </div>
            <input
              type="range"
              min="4.0"
              max="12.0"
              step="0.1"
              value={speed}
              onChange={(e) => setSpeed(parseFloat(e.target.value))}
              className="w-full accent-amber-500 cursor-pointer"
            />
            <div className="flex justify-between text-[10px] font-mono text-zinc-500">
              <span>4.0 MPH (Jog)</span>
              <span>8.0 MPH (Tempo)</span>
              <span>12.0 MPH (Sprint)</span>
            </div>
          </div>

          {/* Engine Output JSON Box */}
          <div className="mt-6 p-4 rounded-xl bg-black border border-zinc-800/80 font-mono text-xs text-zinc-400 overflow-x-auto">
            <div className="text-[10px] uppercase text-amber-500/80 mb-2">// TR3D Engine Realtime Telemetry Payload</div>
            <pre>
              <code>
                {JSON.stringify(
                  {
                    race: current.name,
                    target_incline: current.incline,
                    treadmill_speed_mph: speed,
                    estimated_hr_bpm: simulatedHR,
                    caloric_burn_rate_hr: estimatedCalories,
                    engine_status: "OPTIMAL_SIMULATION",
                  },
                  null,
                  2
                )}
              </code>
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
};

// ==========================================
// 5. CLOSING CTA & FOOTER
// ==========================================
const ClosingCTA = () => (
  <section className="py-24 border-t border-zinc-800/50 bg-gradient-to-b from-zinc-950 via-zinc-900 to-zinc-950 text-center relative overflow-hidden">
    <div className="container mx-auto px-4 md:px-8 relative z-10">
      <div className="max-w-3xl mx-auto">
        <h2 className="text-4xl md:text-5xl font-extrabold text-white tracking-tight leading-tight">
          Ready to Power Your Running Platform?
        </h2>
        <p className="mt-4 text-zinc-400 text-base md:text-lg font-light">
          Deploy the TR3D engine today and turn every treadmill into a precision marathon coach.
        </p>
        <div className="mt-8 flex justify-center gap-4">
          <Button asChild variant="default" size="lg" href={BOOK_A_CALL_URL}>
            <Mail className="mr-2 h-4 w-4" /> Book Integration Call
          </Button>
        </div>
      </div>
    </div>
  </section>
);

const Footer = () => (
  <footer className="py-12 border-t border-zinc-800 bg-zinc-950 text-xs text-zinc-500">
    <div className="container mx-auto px-4 md:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
      <div className="flex items-center gap-4">
        <Wordmark />
        <span>© {new Date().getFullYear()} TR3D Systems Inc. All rights reserved.</span>
      </div>
      <div className="flex gap-6">
        <a href="#different" className="hover:text-zinc-300 transition-colors">The Difference</a>
        <a href="#how" className="hover:text-zinc-300 transition-colors">How It Works</a>
        <a href="#services" className="hover:text-zinc-300 transition-colors">Engine API</a>
        <a href={BOOK_A_CALL_URL} className="hover:text-zinc-300 transition-colors">Contact</a>
      </div>
    </div>
  </footer>
);

// ==========================================
// MAIN COMPONENT EXPORT
// ==========================================
const App = () => (
  <main className="relative min-h-screen bg-zinc-950 text-zinc-100 font-sans selection:bg-amber-500 selection:text-zinc-950">
    <AetherFlowBackground />
    <Nav />
    <Hero />
    <Different />
    <HowItWorks />
    <WhyNow />
    <Services />
    <Monetize />
    <Audience />
    <Why />
    <Demo />
    <ClosingCTA />
    <Footer />
  </main>
);

export default App;
